"""Staging, auditing and committing an uploaded production.

The central decision of this module, written down so it is not accidentally
reversed by a later change:

    **Constraint violations found in an uploaded schedule do not block the
    import.**

Real productions arrive with broken boards. That is the entire point. A 1st AD
whose Tuesday gives the crew nine and a half hours of turnaround already knows
something is wrong and cannot prove it; surfacing that is the first value PRI
delivers, before any disruption and before any agent call. Refusing the upload
would make PRI useless to exactly the people who need it most.

``can_commit`` is true when there are zero **blocking errors**, however many
constraint violations were found.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel
from sqlalchemy import text

from pri.domain.models import ConstraintViolation, ProductionState
from pri.engine.constraints.validator import validate
from pri.importer.errors import ImportIssue, error
from pri.importer.limits import sanitise_filename
from pri.importer.parser import ImportSummary, parse_workbook
from pri.persistence.errors import PersistenceError
from pri.persistence.serialization import snapshot_to_state, state_to_snapshot

if TYPE_CHECKING:
    from pri.persistence.repository import PriRepository

__all__ = [
    "STAGING_TTL",
    "ImportNotFoundError",
    "ImportReport",
    "ImportService",
    "ImportStatus",
]

_log = structlog.get_logger(__name__)

#: How long a pending review survives before ``purge_expired`` removes it. Long
#: enough for a coordinator to forward the link to a producer and get an answer
#: the next morning; short enough that the table does not become a file store.
STAGING_TTL = timedelta(hours=24)


class ImportStatus:
    """Lifecycle of a staged upload."""

    PENDING_REVIEW = "PENDING_REVIEW"
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class ImportNotFoundError(PersistenceError):
    """No staging row with that id."""

    def __init__(self, staging_id: str) -> None:
        super().__init__(f"No import with id {staging_id!r}")
        self.staging_id = staging_id


class ImportReport(BaseModel, frozen=True):
    """Everything the review screen needs, and everything the API returns.

    Inputs:
        existing_violations: Constraint violations found in the **uploaded**
                             schedule. Informational. They never block.
        can_commit:          Zero blocking errors. Independent of
                             ``existing_violations``.
    """

    staging_id: str
    filename: str
    uploaded_at: datetime
    status: str
    summary: ImportSummary
    errors: tuple[ImportIssue, ...] = ()
    warnings: tuple[ImportIssue, ...] = ()
    existing_violations: tuple[ConstraintViolation, ...] = ()
    violation_counts_by_code: dict[str, int] = {}
    can_commit: bool = False
    production_id: str | None = None
    committed_version: int | None = None
    duplicate_of: str | None = None

    @property
    def blocking_count(self) -> int:
        return len(self.errors)


@dataclass(frozen=True, slots=True)
class _StagedRow:
    """One row from ``import_staging``, decoded."""

    id: str
    production_id: str | None
    filename: str
    uploaded_at: datetime
    status: str
    report: dict[str, Any]
    parsed_payload: str | None
    committed_version: int | None


class ImportService:
    """Stage, review, commit and reject uploaded productions.

    Inputs (constructor):
        repo: The persistence repository, used for the production-exists check
              and for committing version 1.
    """

    def __init__(self, repo: PriRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Staging
    # ------------------------------------------------------------------

    async def stage_import(
        self, file_bytes: bytes, filename: str, uploaded_by: str
    ) -> ImportReport:
        """Parse an upload, audit it, and hold it for review.

        The order matters: limits before a single cell is read, then parse,
        then — only if the parse produced a usable state — the constraint
        validator, whose findings are reported and not enforced.

        Inputs:
            file_bytes:  The upload.
            filename:    Original name; sanitised before storage.
            uploaded_by: Identity recorded on the staging row.

        Outputs:
            An :class:`ImportReport`. Its ``status`` is ``FAILED`` when the
            workbook could not be parsed and ``PENDING_REVIEW`` when it could.

        Failure modes:
            Does not raise for a bad file — that is the report's job. Raises
            ``sqlalchemy.exc.SQLAlchemyError`` if the database is unreachable.
        """
        safe_name = sanitise_filename(filename)
        digest = hashlib.sha256(file_bytes).hexdigest()

        existing = await self._find_pending_by_hash(digest)
        if existing is not None:
            _log.info("import_duplicate", staging_id=existing.id, sha256=digest[:12])
            report = self._report_from_row(existing)
            return report.model_copy(update={"duplicate_of": existing.id})

        # openpyxl is synchronous and parsing is CPU-bound, so it runs off the
        # event loop. A 2,000-scene workbook must not stall every other request.
        parsed = await asyncio.to_thread(parse_workbook, file_bytes, safe_name)

        errors = list(parsed.errors)
        violations: tuple[ConstraintViolation, ...] = ()
        production_id: str | None = None

        if parsed.state is not None:
            production_id = parsed.state.production.id
            if await self._production_exists(production_id):
                errors.append(
                    error(
                        "E013",
                        f"A production with the id {production_id!r} already exists.",
                        f"PRI does not merge an upload into an existing production. "
                        f"Change production_id on the production sheet to something "
                        f"new, or delete {production_id!r} first.",
                        sheet="production",
                        row=2,
                        column="production_id",
                        value=production_id,
                    )
                )
            else:
                # The audit that makes the whole module worth building. Runs on
                # the candidate state, reports, and does not block.
                violations = tuple(validate(parsed.state))

        staging_id = f"imp-{uuid.uuid4().hex[:12]}"
        status = ImportStatus.FAILED if errors else ImportStatus.PENDING_REVIEW
        report = ImportReport(
            staging_id=staging_id,
            filename=safe_name,
            uploaded_at=datetime.now(UTC),
            status=status,
            summary=parsed.summary,
            errors=tuple(errors),
            warnings=parsed.warnings,
            existing_violations=violations,
            violation_counts_by_code=_count_by_code(violations),
            can_commit=not errors and parsed.state is not None,
            production_id=production_id,
        )

        await self._insert(
            staging_id=staging_id,
            production_id=production_id,
            filename=safe_name,
            uploaded_by=uploaded_by,
            byte_size=len(file_bytes),
            digest=digest,
            state=parsed.state if not errors else None,
            report=report,
            status=status,
        )

        _log.info(
            "import_staged",
            staging_id=staging_id,
            status=status,
            errors=len(errors),
            warnings=len(parsed.warnings),
            violations=len(violations),
        )
        return report

    async def get_import(self, staging_id: str) -> ImportReport:
        """Return a staged import's report.

        Failure modes:
            Raises :class:`ImportNotFoundError` for an unknown id.
        """
        row = await self._fetch(staging_id)
        if row is None:
            raise ImportNotFoundError(staging_id)
        return self._report_from_row(row)

    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------

    async def commit_import(self, staging_id: str, confirmed_by: str) -> tuple[str, int]:
        """Commit a staged import as version 1 of a new production.

        Idempotent: committing twice returns the same version and writes
        exactly one ``state_versions`` row. The second call is a retry after a
        dropped connection far more often than it is a mistake.

        Inputs:
            staging_id:   The row to commit.
            confirmed_by: Who confirmed. Recorded in the audit log alongside the
                          file's sha256, so a committed production can always be
                          traced to a person and a file.

        Outputs:
            ``(production_id, version)``.

        Failure modes:
            Raises :class:`ImportNotFoundError` for an unknown id, and
            :class:`~pri.persistence.errors.PersistenceError` for a row that is
            not ``PENDING_REVIEW`` — a rejected or failed import must not be
            resurrected by guessing its id.
        """
        row = await self._fetch(staging_id)
        if row is None:
            raise ImportNotFoundError(staging_id)

        if row.status == ImportStatus.COMMITTED:
            if row.production_id is None or row.committed_version is None:
                raise PersistenceError(
                    f"Import {staging_id!r} is marked committed but records no version"
                )
            return row.production_id, row.committed_version

        if row.status != ImportStatus.PENDING_REVIEW:
            raise PersistenceError(
                f"Import {staging_id!r} is {row.status}; only a PENDING_REVIEW import "
                f"can be committed."
            )
        if row.parsed_payload is None:
            raise PersistenceError(f"Import {staging_id!r} holds no parsed state")

        state = snapshot_to_state(row.parsed_payload)
        version = await self._repo.commit_state(state, event_id=None)

        sha = await self._sha_of(staging_id)
        await self._repo.append_audit(
            state.production.id,
            confirmed_by,
            "import.committed",
            staging_id,
            {
                "filename": row.filename,
                "sha256": sha,
                "version": version,
                "scenes": len(state.scenes),
                "shooting_days": len(state.schedule.days),
            },
        )
        await self._mark_committed(staging_id, state.production.id, version)

        _log.info(
            "import_committed",
            staging_id=staging_id,
            production_id=state.production.id,
            version=version,
            confirmed_by=confirmed_by,
        )
        return state.production.id, version

    async def reject_import(self, staging_id: str, reason: str, rejected_by: str) -> None:
        """Mark a staged import rejected, with the reason kept in the report.

        Failure modes:
            Raises :class:`ImportNotFoundError` for an unknown id.
        """
        row = await self._fetch(staging_id)
        if row is None:
            raise ImportNotFoundError(staging_id)

        report = dict(row.report)
        report["status"] = ImportStatus.REJECTED
        report["can_commit"] = False
        report["rejection_reason"] = reason
        report["rejected_by"] = rejected_by

        async with self._session() as session:
            await session.execute(
                text(
                    """
                    UPDATE import_staging
                       SET status = 'REJECTED',
                           report = CAST(:report AS jsonb),
                           parsed_payload = NULL
                     WHERE id = :id
                    """
                ),
                {"id": staging_id, "report": json.dumps(report, default=str)},
            )
        _log.info("import_rejected", staging_id=staging_id, rejected_by=rejected_by)

    async def purge_expired(self) -> int:
        """Delete pending reviews past their expiry.

        Only ``PENDING_REVIEW`` rows are swept. A committed import is the
        provenance record for a live production and is kept.

        Outputs:
            How many rows were removed.
        """
        async with self._session() as session:
            result = await session.execute(
                text(
                    """
                    DELETE FROM import_staging
                     WHERE status = 'PENDING_REVIEW'
                       AND expires_at IS NOT NULL
                       AND expires_at < NOW()
                    """
                )
            )
            removed = int(result.rowcount or 0)
        if removed:
            _log.info("import_staging_purged", removed=removed)
        return removed

    # ------------------------------------------------------------------
    # Persistence plumbing
    # ------------------------------------------------------------------

    def _session(self) -> Any:
        return self._repo._sf()

    async def _production_exists(self, production_id: str) -> bool:
        async with self._session() as session:
            result = await session.execute(
                text("SELECT 1 FROM productions WHERE id = :pid"),
                {"pid": production_id},
            )
            return result.first() is not None

    async def _insert(
        self,
        *,
        staging_id: str,
        production_id: str | None,
        filename: str,
        uploaded_by: str,
        byte_size: int,
        digest: str,
        state: ProductionState | None,
        report: ImportReport,
        status: str,
    ) -> None:
        payload = state_to_snapshot(state)[0] if state is not None else None
        expires = datetime.now(UTC) + STAGING_TTL if status == ImportStatus.PENDING_REVIEW else None
        async with self._session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO import_staging
                        (id, production_id, filename, uploaded_at, uploaded_by,
                         byte_size, sha256, parsed_payload, report, status, expires_at)
                    VALUES
                        (:id, :production_id, :filename, NOW(), :uploaded_by,
                         :byte_size, :sha256, CAST(:payload AS jsonb),
                         CAST(:report AS jsonb), :status, :expires_at)
                    """
                ),
                {
                    "id": staging_id,
                    "production_id": production_id,
                    "filename": filename,
                    "uploaded_by": uploaded_by,
                    "byte_size": byte_size,
                    "sha256": digest,
                    "payload": payload,
                    "report": report.model_dump_json(),
                    "status": status,
                    "expires_at": expires,
                },
            )

    async def _fetch(self, staging_id: str) -> _StagedRow | None:
        async with self._session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, production_id, filename, uploaded_at, status,
                           report, parsed_payload, committed_version
                      FROM import_staging
                     WHERE id = :id
                    """
                ),
                {"id": staging_id},
            )
            row = result.mappings().first()
        return _decode(row) if row is not None else None

    async def _find_pending_by_hash(self, digest: str) -> _StagedRow | None:
        async with self._session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, production_id, filename, uploaded_at, status,
                           report, parsed_payload, committed_version
                      FROM import_staging
                     WHERE sha256 = :sha AND status = 'PENDING_REVIEW'
                     ORDER BY uploaded_at DESC
                     LIMIT 1
                    """
                ),
                {"sha": digest},
            )
            row = result.mappings().first()
        return _decode(row) if row is not None else None

    async def _sha_of(self, staging_id: str) -> str:
        async with self._session() as session:
            result = await session.execute(
                text("SELECT sha256 FROM import_staging WHERE id = :id"),
                {"id": staging_id},
            )
            row = result.first()
        return str(row[0]) if row else ""

    async def _mark_committed(self, staging_id: str, production_id: str, version: int) -> None:
        async with self._session() as session:
            await session.execute(
                text(
                    """
                    UPDATE import_staging
                       SET status = 'COMMITTED',
                           production_id = :pid,
                           committed_version = :version,
                           expires_at = NULL,
                           report = jsonb_set(
                               jsonb_set(report, '{status}', '"COMMITTED"'),
                               '{committed_version}',
                               to_jsonb(CAST(:version AS integer)))
                     WHERE id = :id
                    """
                ),
                {"id": staging_id, "pid": production_id, "version": version},
            )

    def _report_from_row(self, row: _StagedRow) -> ImportReport:
        return ImportReport.model_validate(row.report)


def _decode(row: Any) -> _StagedRow:
    report = row["report"]
    if isinstance(report, str):
        report = json.loads(report)
    payload = row["parsed_payload"]
    if payload is not None and not isinstance(payload, str):
        payload = json.dumps(payload)
    return _StagedRow(
        id=str(row["id"]),
        production_id=row["production_id"],
        filename=str(row["filename"]),
        uploaded_at=row["uploaded_at"],
        status=str(row["status"]),
        report=report,
        parsed_payload=payload,
        committed_version=row["committed_version"],
    )


def _count_by_code(violations: tuple[ConstraintViolation, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for violation in violations:
        counts[violation.code] = counts.get(violation.code, 0) + 1
    return dict(sorted(counts.items()))
