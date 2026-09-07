"""The guarded execution path.

Architecture law 4: no consequential mutation may bypass this sequence.

    1. validate_state         head version still matches the plan's base
    2. validate_constraints   re-run the validator, never trust a cached verdict
    3. validate_policy        does this class of change require approval?
    4. verify_authorization   is this approver allowed to make it?
    5. request_approval       is there an APPROVED row, from that approver?
    6. execute                commit atomically, regenerate artifacts
    7. verify_result          six checks; a failure un-writes the version

Each step writes to ``audit_log`` **before** the next one runs, so a run that
dies mid-sequence leaves a record of exactly how far it got.  Every failure
raises a typed exception naming its step — a caller should never have to parse
a message to find out which gate closed.

Step 7 is the interesting one.  ``state_versions`` is append-only, so "roll
back" cannot mean DELETE.  Instead the verification runs inside the same
transaction as the INSERT, via ``commit_state``'s ``post_insert`` hook: if the
checks fail the transaction aborts and the version was never written at all.
The session is then parked in ``HUMAN_REVIEW`` rather than silently retried.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from pri.domain.models import (
    CandidatePlan,
    Move,
    MoveSceneToDay,
    ProductionState,
    RelocateScene,
    ShiftCallTime,
    SwapDays,
)
from pri.engine.constraints.validator import Policy, load_policy, validate
from pri.engine.verification.verify import (
    VerificationExpectation,
    VerificationReport,
    verify,
)
from pri.persistence.errors import StaleStateError
from pri.persistence.serialization import state_to_snapshot

if TYPE_CHECKING:
    from pathlib import Path

    from pri.persistence.repository import PriRepository

__all__ = [
    "STEPS",
    "ApprovalRequiredError",
    "AuthorizationError",
    "ConstraintViolationError",
    "ExecutionError",
    "PolicyViolationError",
    "TransitionError",
    "TransitionResult",
    "TransitionService",
    "VerificationFailedError",
]

#: The sequence, in order. Exposed so the governance screen can render it as a
#: seven-step checklist without hard-coding the names in the frontend.
STEPS: tuple[str, ...] = (
    "validate_state",
    "validate_constraints",
    "validate_policy",
    "verify_authorization",
    "request_approval",
    "execute",
    "verify_result",
)

_HUMAN_REVIEW_ACTION = "transition.human_review"
_COMPLETED_ACTION = "transition.completed"


# ---------------------------------------------------------------------------
# Typed failures — one per gate
# ---------------------------------------------------------------------------


class TransitionError(RuntimeError):
    """Base class for a transition that was stopped at a named step.

    Inputs:
        step:    The step in :data:`STEPS` that refused.
        message: What went wrong, in terms a producer could read.
    """

    step: str = "unknown"

    def __init__(self, message: str) -> None:
        super().__init__(f"[{self.step}] {message}")
        self.message = message


class ConstraintViolationError(TransitionError):
    """Step 2: the resulting state does not satisfy the constraint rules.

    Carries the rule codes so the API can surface them without re-validating.
    """

    step = "validate_constraints"

    def __init__(self, message: str, codes: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.codes = codes


class PolicyViolationError(TransitionError):
    """Step 3: policy forbids making this change through this path."""

    step = "validate_policy"


class AuthorizationError(TransitionError):
    """Step 4: the approver is absent or not permitted."""

    step = "verify_authorization"


class ApprovalRequiredError(TransitionError):
    """Step 5: no APPROVED decision exists for this plan."""

    step = "request_approval"


class ExecutionError(TransitionError):
    """Step 6: the commit itself failed for a reason that is not a stale head."""

    step = "execute"


class VerificationFailedError(TransitionError):
    """Step 7: the committed state did not verify, so it was not committed.

    Carries the report so the verification screen can show which of the six
    checks failed rather than just saying "something went wrong".
    """

    step = "verify_result"

    def __init__(self, message: str, report: VerificationReport) -> None:
        super().__init__(message)
        self.report = report


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


class TransitionResult(BaseModel, frozen=True):
    """What an execution produced.

    Inputs:
        session_id:      The recovery session executed.
        plan_id:         The plan that was applied.
        base_version:    The version it was applied to.
        new_version:     The version that was written.
        digest:          Snapshot digest of the new version.
        verification:    The six checks. Always populated — a replayed
                         execution re-verifies the stored state rather than
                         returning an empty report, because "we already did
                         this" is not the same claim as "and it was correct".
        audit_entries:   One entry per step completed, in order.
        artifacts:       Paths of the artifacts regenerated for the new version.
        replayed:        ``True`` when this is the recorded outcome of an
                         earlier identical call rather than a fresh execution.
    """

    session_id: str
    plan_id: str
    base_version: int
    new_version: int
    digest: str
    verification: VerificationReport
    audit_entries: tuple[str, ...] = STEPS
    artifacts: tuple[str, ...] = ()
    replayed: bool = False

    @property
    def steps_completed(self) -> tuple[str, ...]:
        """Alias for :attr:`audit_entries`, named the way the UI reads it."""
        return self.audit_entries


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TransitionService:
    """Executes an approved plan, or refuses and says which gate refused.

    Inputs (constructor):
        repo:            The persistence repository.
        policy:          Constraint and approval policy; loaded from
                         ``config/policies.yaml`` when omitted.
        permitted_approvers: Approver identities allowed to authorise a
                         schedule change.  ``None`` means any named approver is
                         acceptable, which is the demo posture — a real
                         deployment passes the producer list.
        artifact_root:   Where call sheets are written.
    """

    def __init__(
        self,
        repo: PriRepository,
        *,
        policy: Policy | None = None,
        permitted_approvers: frozenset[str] | None = None,
        artifact_root: Path | None = None,
    ) -> None:
        self._repo = repo
        self._policy = policy if policy is not None else load_policy()
        self._permitted = permitted_approvers
        self._artifact_root = artifact_root

    # ------------------------------------------------------------------

    async def execute(
        self,
        session_id: str,
        plan_id: str,
        approver: str,
        authorization: str = "",
    ) -> TransitionResult:
        """Run the seven steps and commit, or raise naming the step that stopped.

        Inputs:
            session_id:    The recovery session holding the plan.
            plan_id:       The candidate plan to apply.
            approver:      Identity of the person authorising the change.
            authorization: Opaque credential accompanying the approval.  It is
                           recorded in the audit trail but never logged in full.

        Outputs:
            A :class:`TransitionResult`.  Calling twice with the same
            ``(session_id, plan_id)`` returns the first result with
            ``replayed=True`` and writes no second version.

        Failure modes:
            Raises :exc:`~pri.persistence.errors.StaleStateError` (step 1),
            :exc:`ConstraintViolationError` (2), :exc:`PolicyViolationError` (3),
            :exc:`AuthorizationError` (4), :exc:`ApprovalRequiredError` (5), or
            :exc:`VerificationFailedError` (7).
        """
        session_row = await self._repo.get_session(session_id)
        production_id = str(session_row["production_id"])

        replay = await self._find_completed(production_id, session_id, plan_id)
        if replay is not None:
            return replay

        plan = await self._load_plan(session_id, plan_id)
        completed: list[str] = []

        # ── 1 · validate_state ──────────────────────────────────────────
        head = await self._repo.get_current_state(production_id)
        if head.version != plan.base_version:
            await self._audit(
                production_id,
                approver,
                "transition.rejected",
                f"{session_id}:{plan_id}",
                {"step": "validate_state", "head": head.version, "base": plan.base_version},
            )
            raise StaleStateError(production_id, plan.base_version, head.version)
        await self._step_ok(production_id, approver, session_id, plan_id, STEPS[0])
        completed.append(STEPS[0])

        # ── 2 · validate_constraints ────────────────────────────────────
        # Recomputed here on purpose. The verdict stored when the plan was
        # generated described a different head; between then and now anything
        # could have changed.
        resulting = head.apply(list(plan.moves))
        violations = validate(resulting, policy=self._policy)
        hard = [v for v in violations if v.severity == "HARD"]
        if hard:
            codes = tuple(sorted({v.code for v in hard}))
            await self._audit(
                production_id,
                approver,
                "transition.rejected",
                f"{session_id}:{plan_id}",
                {"step": STEPS[1], "codes": list(codes)},
            )
            first = hard[0]
            raise ConstraintViolationError(
                f"Plan {plan.label} no longer satisfies {first.code}: "
                f"observed {first.observed}, required {first.required}",
                codes,
            )
        await self._step_ok(production_id, approver, session_id, plan_id, STEPS[1])
        completed.append(STEPS[1])

        # ── 3 · validate_policy ─────────────────────────────────────────
        approval_required = bool(
            self._policy.get("approval", {}).get("schedule_change", {}).get("required", True)
        )
        await self._step_ok(
            production_id,
            approver,
            session_id,
            plan_id,
            STEPS[2],
            {"approval_required": approval_required},
        )
        completed.append(STEPS[2])

        # ── 4 · verify_authorization ────────────────────────────────────
        if approval_required:
            if not approver or not approver.strip():
                await self._reject(production_id, approver, session_id, plan_id, STEPS[3])
                raise AuthorizationError("No approver supplied for a change that requires one")
            if self._permitted is not None and approver not in self._permitted:
                await self._reject(production_id, approver, session_id, plan_id, STEPS[3])
                raise AuthorizationError(
                    f"{approver!r} is not permitted to approve schedule changes"
                )
        await self._step_ok(
            production_id,
            approver,
            session_id,
            plan_id,
            STEPS[3],
            {"authorization_present": authorization is not None},
        )
        completed.append(STEPS[3])

        # ── 5 · request_approval ────────────────────────────────────────
        if approval_required:
            approval = await self._repo.get_approval(session_id, plan_id)
            if approval is None:
                await self._reject(production_id, approver, session_id, plan_id, STEPS[4])
                raise ApprovalRequiredError(
                    f"No approval recorded for plan {plan.label} in session {session_id}"
                )
            if str(approval["decision"]) != "APPROVED":
                await self._reject(production_id, approver, session_id, plan_id, STEPS[4])
                raise ApprovalRequiredError(
                    f"Plan {plan.label} was {str(approval['decision']).lower()}, not approved"
                )
            if str(approval["approver"]) != approver:
                await self._reject(production_id, approver, session_id, plan_id, STEPS[4])
                raise ApprovalRequiredError(
                    f"Approval on record is from {approval['approver']!r}, "
                    f"but execution was requested by {approver!r}"
                )
        await self._step_ok(production_id, approver, session_id, plan_id, STEPS[4])
        completed.append(STEPS[4])

        # ── 6 · execute, and 7 · verify_result inside the same transaction ──
        artifacts = self._regenerate_artifacts(resulting)
        report_holder: dict[str, VerificationReport] = {}

        async def _verify_before_commit(committed: ProductionState, digest: str) -> None:
            """Step 7, run while the INSERT can still be rolled back."""
            report = verify(
                committed,
                VerificationExpectation(
                    base_state=head,
                    plan=plan,
                    expected_digest=digest,
                    artifact_kinds=tuple(a.kind for a in artifacts),
                ),
                policy=self._policy,
            )
            report_holder["report"] = report
            if not report.valid:
                failed = ", ".join(f"{c.code} {c.name}" for c in report.failures)
                raise VerificationFailedError(
                    f"Committed state failed verification ({failed}); the write was rolled back",
                    report,
                )

        await self._audit(
            production_id,
            approver,
            "transition.executing",
            f"{session_id}:{plan_id}",
            {"step": STEPS[5], "target_version": resulting.version},
        )

        try:
            new_version = await self._repo.commit_state(
                resulting,
                event_id=session_row.get("event_id"),
                post_insert=_verify_before_commit,
            )
        except VerificationFailedError:
            await self._repo.set_session_status(session_id, "OPEN")
            await self._audit(
                production_id,
                approver,
                _HUMAN_REVIEW_ACTION,
                f"{session_id}:{plan_id}",
                {
                    "step": STEPS[6],
                    "failures": [c.code for c in report_holder["report"].failures],
                    "rolled_back": True,
                },
            )
            raise

        completed.extend((STEPS[5], STEPS[6]))
        report = report_holder["report"]

        for artifact in artifacts:
            await self._repo.record_artifact(
                production_id, new_version, artifact.kind, artifact.path
            )

        digest = artifacts[0].digest if artifacts else ""
        await self._audit(
            production_id,
            approver,
            _COMPLETED_ACTION,
            f"{session_id}:{plan_id}",
            {
                "base_version": plan.base_version,
                "new_version": new_version,
                "digest": digest,
                "artifacts": [a.path for a in artifacts],
                "checks": [c.code for c in report.checks],
            },
        )

        return TransitionResult(
            session_id=session_id,
            plan_id=plan_id,
            base_version=plan.base_version,
            new_version=new_version,
            digest=digest,
            verification=report,
            audit_entries=tuple(completed),
            artifacts=tuple(a.path for a in artifacts),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _regenerate_artifacts(self, state: ProductionState) -> list[Any]:
        """Render call sheets for the new version (step 6, before the commit).

        Written to disk ahead of the transaction so that V6 has something real
        to check.  A rolled-back transaction leaves orphaned PDFs under a
        version directory that no state row references — harmless, and cheaper
        than the alternative of a two-phase write.
        """
        from pri.artifacts.call_sheet import regenerate_all

        return regenerate_all(state, output_root=self._artifact_root)

    async def _load_plan(self, session_id: str, plan_id: str) -> CandidatePlan:
        """Rebuild the stored candidate plan from its database row."""
        row = await self._repo.get_candidate(session_id, plan_id)
        if row is None:
            raise ApprovalRequiredError(
                f"Plan {plan_id!r} is not a candidate of session {session_id!r}"
            )
        raw_moves = row["moves"]
        if isinstance(raw_moves, str):
            raw_moves = json.loads(raw_moves)

        session_row = await self._repo.get_session(session_id)
        return CandidatePlan(
            id=str(row["id"]),
            label=str(row["label"]),
            base_version=int(session_row["base_version"]),
            moves=tuple(_move_from_dict(m) for m in raw_moves),
            rationale_hint=None,
        )

    async def _find_completed(
        self, production_id: str, session_id: str, plan_id: str
    ) -> TransitionResult | None:
        """Return the recorded outcome of an earlier identical execution.

        Idempotency is read out of the audit log rather than kept in memory:
        the guarantee has to survive a process restart, and a retry after a
        dropped connection is the case it exists for.
        """
        subject = f"{session_id}:{plan_id}"
        for entry in await self._repo.get_audit(production_id, limit=500):
            if entry["action"] != _COMPLETED_ACTION or entry["subject"] != subject:
                continue
            detail = entry["detail"]
            if isinstance(detail, str):
                detail = json.loads(detail)

            new_version = int(detail.get("new_version", 0))
            base_version = int(detail.get("base_version", 0))
            report = await self._reverify(production_id, base_version, new_version)
            return TransitionResult(
                session_id=session_id,
                plan_id=plan_id,
                base_version=base_version,
                new_version=new_version,
                digest=str(detail.get("digest", "")),
                verification=report,
                audit_entries=STEPS,
                artifacts=tuple(detail.get("artifacts", [])),
                replayed=True,
            )
        return None

    async def _reverify(
        self, production_id: str, base_version: int, new_version: int
    ) -> VerificationReport:
        """Re-run the six checks against a version that was committed earlier.

        A replayed execution answers "this already happened" — but the caller
        still asked whether the result is sound, and reading a cached verdict
        back out of a table would only prove that something once wrote it there.
        """
        committed = await self._repo.get_state(production_id, new_version)
        base = await self._repo.get_state(production_id, base_version)
        artifacts = await self._repo.get_artifacts(production_id, new_version)
        _, digest = state_to_snapshot(committed)
        return verify(
            committed,
            VerificationExpectation(
                base_state=base,
                plan=CandidatePlan(
                    id=f"replay-v{new_version}",
                    label=f"v{new_version}",
                    base_version=base_version,
                    moves=(),
                    rationale_hint=None,
                ),
                expected_digest=digest,
                artifact_kinds=tuple(str(a["kind"]) for a in artifacts),
            ),
            policy=self._policy,
        )

    async def _audit(
        self,
        production_id: str,
        actor: str,
        action: str,
        subject: str,
        detail: dict[str, object],
    ) -> None:
        await self._repo.append_audit(production_id, actor, action, subject, detail)

    async def _step_ok(
        self,
        production_id: str,
        actor: str,
        session_id: str,
        plan_id: str,
        step: str,
        extra: dict[str, object] | None = None,
    ) -> None:
        """Record that a gate opened, before the next one is tried."""
        detail: dict[str, object] = {"step": step, "at": datetime.now(UTC).isoformat()}
        if extra:
            detail.update(extra)
        await self._audit(
            production_id, actor, f"transition.{step}", f"{session_id}:{plan_id}", detail
        )

    async def _reject(
        self,
        production_id: str,
        actor: str,
        session_id: str,
        plan_id: str,
        step: str,
    ) -> None:
        await self._audit(
            production_id,
            actor,
            "transition.rejected",
            f"{session_id}:{plan_id}",
            {"step": step},
        )


# ---------------------------------------------------------------------------
# Move rehydration
# ---------------------------------------------------------------------------


def _move_from_dict(raw: dict[str, Any]) -> Move:
    """Rebuild a ``Move`` from its stored JSON form.

    Failure modes:
        Raises ``ValueError`` for an unrecognised ``kind`` — a stored plan the
        current code cannot express must not be silently skipped.
    """
    kind = raw.get("kind")
    if kind == "MoveSceneToDay":
        return MoveSceneToDay.model_validate(raw)
    if kind == "SwapDays":
        return SwapDays.model_validate(raw)
    if kind == "ShiftCallTime":
        return ShiftCallTime.model_validate(raw)
    if kind == "RelocateScene":
        return RelocateScene.model_validate(raw)
    raise ValueError(f"Unknown stored move kind: {kind!r}")
