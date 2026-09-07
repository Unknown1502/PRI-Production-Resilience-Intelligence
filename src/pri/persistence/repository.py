"""Async persistence repository for PRI.

All database access is mediated through :class:`PriRepository`.  Callers
supply a :class:`~pri.persistence.database.SessionFactory`; no module-level
state or singleton engine is held here.

Architecture laws enforced:
- ``state_versions`` is append-only: no UPDATE or DELETE paths exist.
- ``commit_state`` is atomic and rejects writes with a stale parent via
  optimistic concurrency (``SELECT … FOR UPDATE`` on the head row).
- ``record_event`` is idempotent: duplicate ``event_id`` returns ``False``
  instead of raising.
- All numbers (digests, versions) are computed here, never by an LLM.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from pri.domain.models import (
        ConstraintViolation,
        DisruptionEvent,
        EvaluatedPlan,
        Move,
        PlanScore,
        ProductionState,
    )
    from pri.persistence.database import SessionFactory

from pri.persistence.errors import (
    ProductionNotFoundError,
    SessionNotFoundError,
    StaleStateError,
    StateVersionNotFoundError,
)
from pri.persistence.serialization import snapshot_to_state, state_to_snapshot

# ---------------------------------------------------------------------------
# Row-level type aliases (plain dicts returned by SQLAlchemy Core)
# ---------------------------------------------------------------------------

_Row = dict[str, Any]

#: Callback signature for :meth:`PriRepository.commit_state`'s ``post_insert``
#: hook: receives the state just inserted and its digest, inside the still-open
#: transaction.  Raising aborts the write.
PostInsertHook = Callable[["ProductionState", str], Awaitable[None]]


class PriRepository:
    """All persistence operations for PRI.

    Inputs (constructor):
        session_factory: A callable returning an ``async with``-able
                         :class:`~pri.persistence.database.SessionFactory`.

    Every public method opens its own session from the factory and commits
    on exit.  Callers must not share sessions across calls.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    # ------------------------------------------------------------------
    # State reads
    # ------------------------------------------------------------------

    async def get_current_state(self, production_id: str) -> ProductionState:
        """Return the latest committed ``ProductionState`` for a production.

        Inputs:
            production_id: The production to look up.

        Outputs:
            The ``ProductionState`` at the highest version number.

        Failure modes:
            Raises :exc:`~pri.persistence.errors.ProductionNotFoundError` if
            the production has no state versions.
        """
        async with self._sf() as session:
            row = await _fetch_head_row(session, production_id)
            if row is None:
                raise ProductionNotFoundError(production_id)
            return snapshot_to_state(_ensure_str(row["snapshot"]))

    async def get_state(self, production_id: str, version: int) -> ProductionState:
        """Return a specific version of the ``ProductionState``.

        Inputs:
            production_id: The production to look up.
            version:       The exact version to retrieve.

        Outputs:
            The ``ProductionState`` at the requested version.

        Failure modes:
            Raises :exc:`~pri.persistence.errors.StateVersionNotFoundError` if
            the (production_id, version) pair does not exist.
        """
        async with self._sf() as session:
            row = await _fetch_version_row(session, production_id, version)
            if row is None:
                raise StateVersionNotFoundError(production_id, version)
            return snapshot_to_state(_ensure_str(row["snapshot"]))

    # ------------------------------------------------------------------
    # State writes
    # ------------------------------------------------------------------

    async def commit_state(
        self,
        state: ProductionState,
        event_id: str | None = None,
        *,
        post_insert: PostInsertHook | None = None,
    ) -> int:
        """Persist a new ``ProductionState`` version atomically.

        The write is rejected with :exc:`~pri.persistence.errors.StaleStateError`
        if ``state.parent_version`` is not the current head version for the
        production (optimistic concurrency).

        For the root version (``parent_version is None``) the production row
        is upserted so that ``commit_state`` is the single entrypoint for both
        "create production" and "update state".

        The digest is computed here from the canonical JSON before insertion;
        it is stored alongside the snapshot for later verification.

        Inputs:
            state:       A :class:`~pri.domain.models.ProductionState` to persist.
            event_id:    Optional event that triggered this version.
            post_insert: Optional async callback run after the row is inserted
                         but *before* the transaction commits.  Raising from it
                         rolls the insert back.  This is how the transition
                         service gets "verify the result, and if it is wrong the
                         version was never written" without a DELETE — which the
                         append-only law forbids and which would leave a hole in
                         the chain anyway.

        Outputs:
            The new version number (equal to ``state.version``).

        Failure modes:
            Raises :exc:`~pri.persistence.errors.StaleStateError` if another
            writer has already advanced the head since this state was computed.
            Raises :exc:`sqlalchemy.exc.SQLAlchemyError` on infrastructure errors.
        """
        json_str, digest = state_to_snapshot(state)

        async with self._sf() as session:
            # ── Upsert the production catalogue row ──────────────────────
            prod = state.production
            await session.execute(
                text(
                    """
                    INSERT INTO productions
                        (id, title, currency, shoot_start, shoot_end, created_at)
                    VALUES
                        (:id, :title, :currency, :shoot_start, :shoot_end, NOW())
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": prod.id,
                    "title": prod.title,
                    "currency": prod.currency,
                    "shoot_start": prod.shoot_start,
                    "shoot_end": prod.shoot_end,
                },
            )

            # ── Optimistic concurrency check (row-level lock) ─────────────
            head_row = await _fetch_head_row_for_update(session, prod.id)
            actual_head: int | None = head_row["version"] if head_row else None

            # Root commit: parent_version is None → head must not exist.
            if state.parent_version is None:
                if actual_head is not None:
                    raise StaleStateError(prod.id, -1, actual_head)
            else:
                if actual_head != state.parent_version:
                    raise StaleStateError(
                        prod.id,
                        state.parent_version,
                        actual_head if actual_head is not None else 0,
                    )

            # ── Append new version ────────────────────────────────────────
            await session.execute(
                text(
                    """
                    INSERT INTO state_versions
                        (production_id, version, parent_version, event_id,
                         snapshot, digest, created_at)
                    VALUES
                        (:production_id, :version, :parent_version, :event_id,
                         CAST(:snapshot AS jsonb), :digest, NOW())
                    """
                ),
                {
                    "production_id": prod.id,
                    "version": state.version,
                    "parent_version": state.parent_version,
                    "event_id": event_id,
                    "snapshot": json_str,
                    "digest": digest,
                },
            )

            if post_insert is not None:
                await post_insert(state, digest)

        return state.version

    # ------------------------------------------------------------------
    # Event ingestion
    # ------------------------------------------------------------------

    async def record_event(self, event: DisruptionEvent) -> bool:
        """Persist a disruption event, skipping duplicates.

        Idempotency is guaranteed via the ``UNIQUE(event_id)`` constraint.  A
        duplicate insert returns ``False`` without raising.

        Inputs:
            event: A :class:`~pri.domain.models.DisruptionEvent` to record.

        Outputs:
            ``True`` if the event was newly inserted, ``False`` if it was
            already present.

        Failure modes:
            Raises :exc:`sqlalchemy.exc.SQLAlchemyError` on infrastructure errors.
        """
        async with self._sf() as session:
            try:
                await session.execute(
                    text(
                        """
                        INSERT INTO events
                            (event_id, production_id, event_type, occurred_at,
                             source, severity, payload)
                        VALUES
                            (:event_id, :production_id, :event_type, :occurred_at,
                             :source, :severity, CAST(:payload AS jsonb))
                        """
                    ),
                    {
                        "event_id": event.event_id,
                        "production_id": event.production_id,
                        "event_type": event.event_type,
                        "occurred_at": event.occurred_at,
                        "source": event.source,
                        "severity": event.severity,
                        "payload": _json_dumps(dict(event.payload)),
                    },
                )
                return True
            except IntegrityError:
                return False

    # ------------------------------------------------------------------
    # Recovery session lifecycle
    # ------------------------------------------------------------------

    async def create_session(
        self,
        production_id: str,
        event_id: str | None,
        base_version: int,
    ) -> str:
        """Create a new recovery session and return its ID.

        Inputs:
            production_id: The production being recovered.
            event_id:      The triggering event (may be ``None``).
            base_version:  The ``ProductionState.version`` recovery is based on.

        Outputs:
            The new session's UUID string.

        Failure modes:
            Raises :exc:`sqlalchemy.exc.SQLAlchemyError` on infrastructure errors.
        """
        session_id = str(uuid.uuid4())
        async with self._sf() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO recovery_sessions
                        (id, production_id, event_id, base_version, status,
                         created_at, updated_at)
                    VALUES
                        (:id, :production_id, :event_id, :base_version,
                         'OPEN', NOW(), NOW())
                    """
                ),
                {
                    "id": session_id,
                    "production_id": production_id,
                    "event_id": event_id,
                    "base_version": base_version,
                },
            )
        return session_id

    async def create_session_with_id(
        self,
        session_id: str,
        production_id: str,
        event_id: str | None,
        base_version: int,
    ) -> str:
        """Create a recovery session under a caller-supplied id.

        The engine mints the session id before anything is written, so that the
        SSE frames, the candidate rows and the response all refer to the same
        identifier.  Re-inserting an existing id is a no-op rather than an
        error: a retried recovery should land on the same session.

        Inputs:
            session_id:    The id to use.
            production_id: The production being recovered.
            event_id:      The triggering event, if any.
            base_version:  The state version recovery is based on.

        Outputs:
            ``session_id``, unchanged.
        """
        async with self._sf() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO recovery_sessions
                        (id, production_id, event_id, base_version, status,
                         created_at, updated_at)
                    VALUES
                        (:id, :production_id, :event_id, :base_version,
                         'OPEN', NOW(), NOW())
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": session_id,
                    "production_id": production_id,
                    "event_id": event_id,
                    "base_version": base_version,
                },
            )
        return session_id

    async def add_candidates(
        self,
        session_id: str,
        plans: list[EvaluatedPlan],
    ) -> None:
        """Persist evaluated candidate plans for a recovery session.

        Inputs:
            session_id: A recovery session that already exists in the database.
            plans:      The evaluated plans to store.

        Failure modes:
            Raises :exc:`~pri.persistence.errors.SessionNotFoundError` if the
            session does not exist (via FK violation caught and re-raised).
            Raises :exc:`sqlalchemy.exc.SQLAlchemyError` on other DB errors.
        """
        async with self._sf() as session:
            await _assert_session_exists(session, session_id)
            for ep in plans:
                await session.execute(
                    text(
                        """
                        INSERT INTO candidate_plans
                            (id, session_id, label, moves, valid,
                             violations, score, pareto_optimal)
                        VALUES
                            (:id, :session_id, :label,
                             CAST(:moves AS jsonb), :valid,
                             CAST(:violations AS jsonb),
                             CAST(:score AS jsonb),
                             :pareto_optimal)
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": ep.plan.id,
                        "session_id": session_id,
                        "label": ep.plan.label,
                        "moves": _json_dumps([_move_to_dict(m) for m in ep.plan.moves]),
                        "valid": ep.valid,
                        "violations": _json_dumps([_violation_to_dict(v) for v in ep.violations]),
                        "score": _json_dumps(_score_to_dict(ep.score)) if ep.score else None,
                        "pareto_optimal": ep.pareto_optimal,
                    },
                )

    async def record_approval(
        self,
        session_id: str,
        plan_id: str,
        approver: str,
        decision: str,
        note: str | None = None,
    ) -> str:
        """Record an approval decision and update the session status.

        Inputs:
            session_id: The recovery session being decided.
            plan_id:    The candidate plan being approved or rejected.
            approver:   Identity of the decision-maker.
            decision:   ``"APPROVED"`` or ``"REJECTED"``.
            note:       Optional free-text note.

        Outputs:
            The new approval record's UUID string.

        Failure modes:
            Raises :exc:`~pri.persistence.errors.SessionNotFoundError` if the
            session does not exist.
            Raises ``ValueError`` if ``decision`` is not ``"APPROVED"`` or
            ``"REJECTED"``.
        """
        if decision not in ("APPROVED", "REJECTED"):
            raise ValueError(f"Invalid decision: {decision!r}")

        approval_id = str(uuid.uuid4())
        new_status = "APPROVED" if decision == "APPROVED" else "REJECTED"

        async with self._sf() as session:
            await _assert_session_exists(session, session_id)
            await session.execute(
                text(
                    """
                    INSERT INTO approvals
                        (id, session_id, plan_id, approver, decision,
                         decided_at, note)
                    VALUES
                        (:id, :session_id, :plan_id, :approver, :decision,
                         NOW(), :note)
                    """
                ),
                {
                    "id": approval_id,
                    "session_id": session_id,
                    "plan_id": plan_id,
                    "approver": approver,
                    "decision": decision,
                    "note": note,
                },
            )
            await session.execute(
                text(
                    """
                    UPDATE recovery_sessions
                       SET status = :status, updated_at = NOW()
                     WHERE id = :session_id
                    """
                ),
                {"status": new_status, "session_id": session_id},
            )

        return approval_id

    async def append_audit(
        self,
        production_id: str,
        actor: str,
        action: str,
        subject: str,
        detail: dict[str, object] | None = None,
    ) -> None:
        """Append an entry to the audit log.

        Inputs:
            production_id: The production context.
            actor:         Who performed the action.
            action:        Machine-readable action code.
            subject:       Identifier of the affected entity.
            detail:        Optional key/value metadata.

        Failure modes:
            Raises :exc:`sqlalchemy.exc.SQLAlchemyError` on infrastructure errors.
        """
        async with self._sf() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO audit_log
                        (production_id, at, actor, action, subject, detail)
                    VALUES
                        (:production_id, NOW(), :actor, :action, :subject,
                         CAST(:detail AS jsonb))
                    """
                ),
                {
                    "production_id": production_id,
                    "actor": actor,
                    "action": action,
                    "subject": subject,
                    "detail": _json_dumps(detail or {}),
                },
            )

    async def record_artifact(
        self,
        production_id: str,
        version: int,
        kind: str,
        path: str,
    ) -> str:
        """Record a generated artifact (PDF, CSV, etc.).

        Inputs:
            production_id: The production this artifact belongs to.
            version:       The ``ProductionState`` version it was generated from.
            kind:          Artifact type (e.g. ``"call_sheet_pdf"``).
            path:          Storage path or URI.

        Outputs:
            The new artifact record's UUID string.

        Failure modes:
            Raises :exc:`sqlalchemy.exc.SQLAlchemyError` on infrastructure errors.
        """
        artifact_id = str(uuid.uuid4())
        async with self._sf() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO artifacts
                        (id, production_id, version, kind, path, created_at)
                    VALUES
                        (:id, :production_id, :version, :kind, :path, NOW())
                    """
                ),
                {
                    "id": artifact_id,
                    "production_id": production_id,
                    "version": version,
                    "kind": kind,
                    "path": path,
                },
            )
        return artifact_id

    # ------------------------------------------------------------------
    # Workflow reads
    #
    # The transition service must re-read what it is about to act on rather
    # than trust what the caller handed it — an approval passed in as a
    # function argument proves nothing about what is in the database.
    # ------------------------------------------------------------------

    async def get_session(self, session_id: str) -> _Row:
        """Return a recovery session row.

        Inputs:
            session_id: The session to load.

        Outputs:
            A dict with ``id``, ``production_id``, ``event_id``,
            ``base_version``, ``status``, ``created_at`` and ``updated_at``.

        Failure modes:
            Raises :exc:`~pri.persistence.errors.SessionNotFoundError` if the
            session does not exist.
        """
        async with self._sf() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, production_id, event_id, base_version, status,
                           created_at, updated_at
                      FROM recovery_sessions
                     WHERE id = :sid
                    """
                ),
                {"sid": session_id},
            )
            row = result.mappings().first()
        if row is None:
            raise SessionNotFoundError(session_id)
        return dict(row)

    async def get_latest_session(self, production_id: str) -> _Row | None:
        """Return the most recent recovery session for a production.

        Exists so the recovery screen can rehydrate. It otherwise derives
        everything from the SSE stream, which means a refresh — or a viewer who
        arrives after the run — sees an empty screen while a completed recovery
        sits in the database. During a live demo that reads as a broken app.

        Outputs:
            The session row, or ``None`` when the production has never had one.
        """
        async with self._sf() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, production_id, event_id, base_version, status,
                           created_at, updated_at
                      FROM recovery_sessions
                     WHERE production_id = :pid
                     ORDER BY created_at DESC, id DESC
                     LIMIT 1
                    """
                ),
                {"pid": production_id},
            )
            row = result.mappings().first()
        return dict(row) if row is not None else None

    async def get_candidates(self, session_id: str) -> list[_Row]:
        """Return every candidate plan stored for a session, in label order.

        Inputs:
            session_id: The recovery session.

        Outputs:
            A list of dicts with ``id``, ``label``, ``moves``, ``valid``,
            ``violations``, ``score`` and ``pareto_optimal``.  JSONB columns
            come back already decoded.

        Failure modes:
            Returns ``[]`` for a session with no candidates; does not raise for
            an unknown session id.
        """
        async with self._sf() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, session_id, label, moves, valid, violations,
                           score, pareto_optimal
                      FROM candidate_plans
                     WHERE session_id = :sid
                     ORDER BY label
                    """
                ),
                {"sid": session_id},
            )
            return [dict(row) for row in result.mappings().all()]

    async def get_candidate(self, session_id: str, plan_id: str) -> _Row | None:
        """Return one candidate plan, or ``None`` if it is not in this session."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, session_id, label, moves, valid, violations,
                           score, pareto_optimal
                      FROM candidate_plans
                     WHERE session_id = :sid AND id = :pid
                    """
                ),
                {"sid": session_id, "pid": plan_id},
            )
            row = result.mappings().first()
        return dict(row) if row is not None else None

    async def get_approval(self, session_id: str, plan_id: str) -> _Row | None:
        """Return the most recent approval decision for a (session, plan) pair.

        Inputs:
            session_id: The recovery session.
            plan_id:    The plan the decision was about.

        Outputs:
            A dict with ``id``, ``approver``, ``decision``, ``decided_at`` and
            ``note``, or ``None`` when no decision has been recorded.

        Failure modes:
            Does not raise for unknown ids.
        """
        async with self._sf() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, session_id, plan_id, approver, decision,
                           decided_at, note
                      FROM approvals
                     WHERE session_id = :sid AND plan_id = :pid
                     ORDER BY decided_at DESC
                     LIMIT 1
                    """
                ),
                {"sid": session_id, "pid": plan_id},
            )
            row = result.mappings().first()
        return dict(row) if row is not None else None

    async def get_artifacts(self, production_id: str, version: int) -> list[_Row]:
        """Return every artifact registered for one state version."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, production_id, version, kind, path, created_at
                      FROM artifacts
                     WHERE production_id = :pid AND version = :ver
                     ORDER BY created_at
                    """
                ),
                {"pid": production_id, "ver": version},
            )
            return [dict(row) for row in result.mappings().all()]

    async def get_artifact(self, artifact_id: str) -> _Row | None:
        """Return one artifact row by id, or ``None``."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, production_id, version, kind, path, created_at
                      FROM artifacts
                     WHERE id = :aid
                    """
                ),
                {"aid": artifact_id},
            )
            row = result.mappings().first()
        return dict(row) if row is not None else None

    async def get_audit(self, production_id: str, limit: int = 200) -> list[_Row]:
        """Return the audit log for a production, newest first.

        Inputs:
            production_id: The production whose log to read.
            limit:         Maximum entries to return.

        Outputs:
            A list of dicts with ``at``, ``actor``, ``action``, ``subject``
            and ``detail``.
        """
        async with self._sf() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, at, actor, action, subject, detail
                      FROM audit_log
                     WHERE production_id = :pid
                     ORDER BY at DESC, id DESC
                     LIMIT :lim
                    """
                ),
                {"pid": production_id, "lim": limit},
            )
            return [dict(row) for row in result.mappings().all()]

    async def get_event(self, event_id: str) -> _Row | None:
        """Return a recorded disruption event, or ``None``."""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    """
                    SELECT event_id, production_id, event_type, occurred_at,
                           source, severity, payload, consumed_at
                      FROM events
                     WHERE event_id = :eid
                    """
                ),
                {"eid": event_id},
            )
            row = result.mappings().first()
        return dict(row) if row is not None else None

    async def set_session_status(self, session_id: str, status: str) -> None:
        """Move a recovery session to a new lifecycle status.

        Inputs:
            session_id: The session to update.
            status:     One of ``OPEN``, ``APPROVED``, ``REJECTED``, ``EXPIRED``.

        Failure modes:
            Raises ``ValueError`` for a status the schema will not accept —
            better a clear error here than an opaque CHECK violation.
        """
        allowed = {"OPEN", "APPROVED", "REJECTED", "EXPIRED"}
        if status not in allowed:
            raise ValueError(
                f"Invalid session status {status!r}; expected one of {sorted(allowed)}"
            )
        async with self._sf() as session:
            await _assert_session_exists(session, session_id)
            await session.execute(
                text(
                    """
                    UPDATE recovery_sessions
                       SET status = :status, updated_at = NOW()
                     WHERE id = :sid
                    """
                ),
                {"status": status, "sid": session_id},
            )

    async def head_version(self, production_id: str) -> int:
        """Return the current head version number for a production.

        Failure modes:
            Raises :exc:`~pri.persistence.errors.ProductionNotFoundError` if the
            production has no committed state.
        """
        async with self._sf() as session:
            row = await _fetch_head_row_for_update(session, production_id)
        if row is None:
            raise ProductionNotFoundError(production_id)
        return int(row["version"])


# ---------------------------------------------------------------------------
# Internal helpers (module-private)
# ---------------------------------------------------------------------------


async def _fetch_head_row(
    session: AsyncSession,
    production_id: str,
) -> _Row | None:
    """Return the highest-version row for a production, or ``None``."""
    result = await session.execute(
        text(
            """
            SELECT version, snapshot
              FROM state_versions
             WHERE production_id = :pid
             ORDER BY version DESC
             LIMIT 1
            """
        ),
        {"pid": production_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _fetch_head_row_for_update(
    session: AsyncSession,
    production_id: str,
) -> _Row | None:
    """Return the highest-version row with a ``FOR UPDATE`` lock, or ``None``."""
    result = await session.execute(
        text(
            """
            SELECT version
              FROM state_versions
             WHERE production_id = :pid
             ORDER BY version DESC
             LIMIT 1
             FOR UPDATE
            """
        ),
        {"pid": production_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _fetch_version_row(
    session: AsyncSession,
    production_id: str,
    version: int,
) -> _Row | None:
    """Return a specific version row, or ``None``."""
    result = await session.execute(
        text(
            """
            SELECT version, snapshot
              FROM state_versions
             WHERE production_id = :pid
               AND version = :ver
            """
        ),
        {"pid": production_id, "ver": version},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _assert_session_exists(
    session: AsyncSession,
    session_id: str,
) -> None:
    """Raise ``SessionNotFoundError`` if the recovery session doesn't exist."""
    result = await session.execute(
        text("SELECT 1 FROM recovery_sessions WHERE id = :sid"),
        {"sid": session_id},
    )
    if result.first() is None:
        raise SessionNotFoundError(session_id)


def _json_dumps(obj: object) -> str:
    """Serialize an object to a compact JSON string.

    Inputs:
        obj: Any JSON-serialisable Python object.

    Outputs:
        Compact JSON string (no extra whitespace).

    Failure modes:
        Raises ``TypeError`` if ``obj`` contains non-serialisable values.
    """
    import json

    return json.dumps(obj, default=_json_default)


def _json_default(obj: object) -> object:
    """Fallback serialiser for types not handled by the stdlib encoder."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)!r} is not JSON serialisable")


def _move_to_dict(move: Move) -> dict[str, object]:
    """Serialize a ``Move`` variant to a plain dict."""
    from pri.domain.models import (
        MoveSceneToDay,
        RelocateScene,
        ShiftCallTime,
        SwapDays,
    )

    if isinstance(move, MoveSceneToDay):
        return {
            "kind": "MoveSceneToDay",
            "scene_id": move.scene_id,
            "target_date": move.target_date.isoformat(),
        }
    if isinstance(move, SwapDays):
        return {
            "kind": "SwapDays",
            "date_a": move.date_a.isoformat(),
            "date_b": move.date_b.isoformat(),
        }
    if isinstance(move, ShiftCallTime):
        return {
            "kind": "ShiftCallTime",
            "date": move.date.isoformat(),
            "new_call_time": move.new_call_time.isoformat(),
        }
    if isinstance(move, RelocateScene):
        return {
            "kind": "RelocateScene",
            "scene_id": move.scene_id,
            "target_location_id": move.target_location_id,
        }
    raise TypeError(f"Unknown Move kind: {type(move)!r}")


def _violation_to_dict(v: ConstraintViolation) -> dict[str, object]:
    """Serialize a ``ConstraintViolation`` to a plain dict."""
    return {
        "code": v.code,
        "severity": v.severity,
        "message": v.message,
        "subject_ids": list(v.subject_ids),
        "observed": v.observed,
        "required": v.required,
    }


def _score_to_dict(score: PlanScore) -> dict[str, object]:
    """Serialize a ``PlanScore`` to a plain dict."""
    return {
        "schedule_delay_days": score.schedule_delay_days,
        "incremental_cost": str(score.incremental_cost),
        "operational_risk": score.operational_risk,
        "affected_scene_count": score.affected_scene_count,
        "crew_disruption_hours": score.crew_disruption_hours,
        "downstream_dependency_impact": score.downstream_dependency_impact,
    }


def _ensure_str(value: object) -> str:
    """Coerce a JSONB value from asyncpg to a JSON string.

    asyncpg returns JSONB columns as already-parsed Python objects (dict/list).
    SQLAlchemy Core with text() bypasses ORM type coercion, so we must handle
    both cases: a raw string (used in tests or future drivers) and a dict
    (returned by asyncpg for JSONB columns).

    Inputs:
        value: The raw value from the DB row mapping — either a ``str`` or a
               Python object already deserialised by the asyncpg JSONB codec.

    Outputs:
        A JSON string suitable for passing to :func:`snapshot_to_state`.

    Failure modes:
        Raises ``TypeError`` if ``value`` is neither a ``str`` nor a JSON-
        serialisable object.
    """
    import json

    if isinstance(value, str):
        return value
    return json.dumps(value)
