"""Staging, the pre-existing violation audit, commit, and expiry.

Against a real PostgreSQL, because the guarantees under test are transactional:
"committing twice writes one row" cannot be verified against a fake.

The central test in this file is
``test_a_broken_schedule_imports_and_reports_its_own_violation``. If that ever
starts asserting the opposite, someone has reversed the design decision the
module exists for.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from pri.importer import export_state
from pri.importer.samples import build_second_unit_state
from pri.importer.service import ImportNotFoundError, ImportService, ImportStatus
from pri.persistence.bootstrap import split_sql_statements
from pri.persistence.database import SessionFactory
from pri.persistence.errors import PersistenceError
from pri.persistence.repository import PriRepository
from pri.persistence.seed import build_state
from tests.importer.conftest import SheetRows, build_xlsx

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_DEFAULT_URL = "postgresql+asyncpg://pri_user:changeme@localhost:5432/pri"
_MIGRATIONS = Path(__file__).parents[2] / "src" / "pri" / "persistence" / "migrations"
_TABLES = """
    import_staging, artifacts, audit_log, approvals, candidate_plans,
    recovery_sessions, state_versions, events, productions
"""

UPLOADER = "1st.ad@production"


@pytest_asyncio.fixture()
async def repo() -> AsyncGenerator[PriRepository, None]:
    """A clean schema with both migrations applied and no productions."""
    engine = create_async_engine(
        os.environ.get("TEST_DATABASE_URL", _DEFAULT_URL), echo=False, pool_pre_ping=False
    )
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLES} CASCADE"))
        for path in sorted(_MIGRATIONS.glob("*.sql")):
            for statement in split_sql_statements(path.read_text()):
                await conn.execute(text(statement))

    yield PriRepository(SessionFactory(engine))

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLES} CASCADE"))
    await engine.dispose()


@pytest.fixture()
def service(repo: PriRepository) -> ImportService:
    return ImportService(repo)


def _tight_turnaround(baseline: dict[str, SheetRows]) -> bytes:
    """A board whose Tuesday leaves the crew nine and a half hours.

    Wrap 22:30, call 08:00 the next morning. Exactly the thing a 1st AD already
    suspects and cannot prove.
    """
    baseline["schedule"][0]["call_time"] = time(12, 0)
    baseline["schedule"][0]["wrap_time"] = time(22, 30)
    baseline["schedule"][1]["call_time"] = time(8, 0)
    baseline["schedule"][1]["wrap_time"] = time(18, 0)
    return build_xlsx(baseline)


class TestTheCentralBehaviour:
    """Constraint violations do not block an import. This is the whole point."""

    async def test_a_broken_schedule_imports_and_reports_its_own_violation(
        self, service: ImportService, baseline: dict[str, SheetRows]
    ) -> None:
        report = await service.stage_import(_tight_turnaround(baseline), "my-board.xlsx", UPLOADER)

        assert report.errors == (), "a broken board is not a broken file"
        assert report.can_commit is True
        assert report.status == ImportStatus.PENDING_REVIEW

        assert len(report.existing_violations) == 1
        violation = report.existing_violations[0]
        assert violation.code == "C001"
        assert violation.observed == "9.5h"
        assert violation.required == ">= 10.0h"
        assert report.violation_counts_by_code == {"C001": 1}

    async def test_it_can_actually_be_committed(
        self, service: ImportService, repo: PriRepository, baseline: dict[str, SheetRows]
    ) -> None:
        report = await service.stage_import(_tight_turnaround(baseline), "my-board.xlsx", UPLOADER)
        production_id, version = await service.commit_import(report.staging_id, "producer")

        assert version == 1
        committed = await repo.get_current_state(production_id)
        assert committed.version == 1
        assert len(committed.scenes) == 3

    async def test_a_clean_board_reports_no_violations(
        self, service: ImportService, baseline: dict[str, SheetRows]
    ) -> None:
        report = await service.stage_import(build_xlsx(baseline), "clean.xlsx", UPLOADER)
        assert report.existing_violations == ()
        assert report.can_commit is True


class TestStaging:
    async def test_a_parse_failure_is_staged_as_failed_and_cannot_commit(
        self, service: ImportService, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["scenes"][0]["location_id"] = "L-99"
        report = await service.stage_import(build_xlsx(baseline), "broken.xlsx", UPLOADER)

        assert report.status == ImportStatus.FAILED
        assert report.can_commit is False
        assert [e.code for e in report.errors] == ["E006"]

    async def test_a_failed_import_cannot_be_committed(
        self, service: ImportService, baseline: dict[str, SheetRows]
    ) -> None:
        baseline["scenes"][0]["location_id"] = "L-99"
        report = await service.stage_import(build_xlsx(baseline), "broken.xlsx", UPLOADER)

        with pytest.raises(PersistenceError, match="PENDING_REVIEW"):
            await service.commit_import(report.staging_id, "producer")

    async def test_the_report_survives_a_round_trip_through_the_database(
        self, service: ImportService, baseline: dict[str, SheetRows]
    ) -> None:
        staged = await service.stage_import(_tight_turnaround(baseline), "my-board.xlsx", UPLOADER)
        fetched = await service.get_import(staged.staging_id)

        assert fetched.staging_id == staged.staging_id
        assert fetched.filename == "my-board.xlsx"
        assert len(fetched.existing_violations) == 1
        assert fetched.existing_violations[0].observed == "9.5h"
        assert fetched.summary.scene_count == 3

    async def test_an_unknown_id_is_reported_as_missing(self, service: ImportService) -> None:
        with pytest.raises(ImportNotFoundError):
            await service.get_import("imp-nope")

    async def test_re_uploading_the_same_bytes_returns_the_first_review(
        self, service: ImportService, baseline: dict[str, SheetRows]
    ) -> None:
        """Prevents a cluttered staging table and a confused user."""
        payload = build_xlsx(baseline)
        first = await service.stage_import(payload, "board.xlsx", UPLOADER)
        second = await service.stage_import(payload, "board-copy.xlsx", UPLOADER)

        assert second.staging_id == first.staging_id
        assert second.duplicate_of == first.staging_id

    async def test_the_filename_is_sanitised_before_it_is_stored(
        self, service: ImportService, baseline: dict[str, SheetRows]
    ) -> None:
        report = await service.stage_import(build_xlsx(baseline), '../../etc/pa"ss.xlsx', UPLOADER)
        assert report.filename == "pass.xlsx"

    async def test_e013_when_the_production_already_exists(
        self, service: ImportService, repo: PriRepository
    ) -> None:
        """Merge is not supported, deliberately, and the error says so."""
        await repo.commit_state(build_state(), event_id=None)

        report = await service.stage_import(export_state(build_state()), "again.xlsx", UPLOADER)
        assert report.can_commit is False
        issue = next(e for e in report.errors if e.code == "E013")
        assert "film-001" in issue.message
        assert "does not merge" in issue.fix_hint


class TestIdempotency:
    async def test_committing_twice_returns_the_same_version(
        self, service: ImportService, baseline: dict[str, SheetRows]
    ) -> None:
        report = await service.stage_import(build_xlsx(baseline), "b.xlsx", UPLOADER)
        first = await service.commit_import(report.staging_id, "producer")
        second = await service.commit_import(report.staging_id, "producer")
        assert first == second

    async def test_committing_twice_writes_exactly_one_state_row(
        self, service: ImportService, repo: PriRepository, baseline: dict[str, SheetRows]
    ) -> None:
        report = await service.stage_import(build_xlsx(baseline), "b.xlsx", UPLOADER)
        production_id, _ = await service.commit_import(report.staging_id, "producer")
        await service.commit_import(report.staging_id, "producer")

        async with repo._sf() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM state_versions WHERE production_id = :pid"),
                {"pid": production_id},
            )
            assert result.scalar_one() == 1


class TestAudit:
    async def test_the_commit_is_audited_with_the_person_and_the_file_hash(
        self, service: ImportService, repo: PriRepository, baseline: dict[str, SheetRows]
    ) -> None:
        report = await service.stage_import(build_xlsx(baseline), "board.xlsx", UPLOADER)
        production_id, _ = await service.commit_import(report.staging_id, "producer@studio")

        entries = await repo.get_audit(production_id)
        entry = next(e for e in entries if e["action"] == "import.committed")
        assert entry["actor"] == "producer@studio"

        detail = entry["detail"]
        assert detail["filename"] == "board.xlsx"
        assert len(detail["sha256"]) == 64
        assert detail["version"] == 1


class TestRejection:
    async def test_a_rejected_import_keeps_its_reason_and_drops_its_payload(
        self, service: ImportService, baseline: dict[str, SheetRows]
    ) -> None:
        report = await service.stage_import(build_xlsx(baseline), "b.xlsx", UPLOADER)
        await service.reject_import(report.staging_id, "Wrong version of the board", "ad")

        fetched = await service.get_import(report.staging_id)
        assert fetched.status == ImportStatus.REJECTED
        assert fetched.can_commit is False

    async def test_a_rejected_import_cannot_be_committed(
        self, service: ImportService, baseline: dict[str, SheetRows]
    ) -> None:
        report = await service.stage_import(build_xlsx(baseline), "b.xlsx", UPLOADER)
        await service.reject_import(report.staging_id, "no", "ad")

        with pytest.raises(PersistenceError):
            await service.commit_import(report.staging_id, "producer")


class TestExpiry:
    async def test_purge_removes_a_stale_pending_review(
        self, service: ImportService, repo: PriRepository, baseline: dict[str, SheetRows]
    ) -> None:
        report = await service.stage_import(build_xlsx(baseline), "old.xlsx", UPLOADER)
        async with repo._sf() as session:
            await session.execute(
                text("UPDATE import_staging SET expires_at = :when WHERE id = :id"),
                {"when": datetime.now(UTC) - timedelta(hours=1), "id": report.staging_id},
            )

        assert await service.purge_expired() == 1
        with pytest.raises(ImportNotFoundError):
            await service.get_import(report.staging_id)

    async def test_purge_leaves_a_committed_import_alone(
        self, service: ImportService, repo: PriRepository, baseline: dict[str, SheetRows]
    ) -> None:
        """A committed import is the provenance record for a live production."""
        report = await service.stage_import(build_xlsx(baseline), "kept.xlsx", UPLOADER)
        await service.commit_import(report.staging_id, "producer")
        async with repo._sf() as session:
            await session.execute(
                text("UPDATE import_staging SET expires_at = :when WHERE id = :id"),
                {"when": datetime.now(UTC) - timedelta(days=30), "id": report.staging_id},
            )

        assert await service.purge_expired() == 0
        assert (await service.get_import(report.staging_id)).status == ImportStatus.COMMITTED

    async def test_purge_leaves_a_fresh_pending_review_alone(
        self, service: ImportService, baseline: dict[str, SheetRows]
    ) -> None:
        report = await service.stage_import(build_xlsx(baseline), "fresh.xlsx", UPLOADER)
        assert await service.purge_expired() == 0
        assert (await service.get_import(report.staging_id)).status == (ImportStatus.PENDING_REVIEW)


class TestBothSamplesImport:
    """Acceptance: both sample workbooks import with zero blocking errors."""

    async def test_the_night_train_sample_imports(self, service: ImportService) -> None:
        report = await service.stage_import(
            export_state(build_state()), "night_train.xlsx", UPLOADER
        )
        assert report.errors == ()
        assert report.can_commit is True
        assert report.summary.scene_count == 13

    async def test_the_second_unit_sample_imports_with_its_planted_violation(
        self, service: ImportService
    ) -> None:
        report = await service.stage_import(
            export_state(build_second_unit_state()), "second_unit.xlsx", UPLOADER
        )
        assert report.errors == ()
        assert report.can_commit is True
        assert report.summary.unit_names == ("MAIN", "SECOND")
        assert report.violation_counts_by_code == {"C001": 1}


class TestImportedProductionIsFullyUsable:
    """Acceptance 2: a disruption runs against an imported production."""

    async def test_a_disruption_runs_against_the_committed_second_unit(
        self, service: ImportService, repo: PriRepository
    ) -> None:
        from pri.engine.simulation.replan import recover
        from pri.importer.samples import second_unit_disruption

        report = await service.stage_import(
            export_state(build_second_unit_state()), "second_unit.xlsx", UPLOADER
        )
        production_id, _ = await service.commit_import(report.staging_id, "producer")

        committed = await repo.get_current_state(production_id)
        result = recover(committed, second_unit_disruption())

        assert result.impact.directly_affected_scene_ids
        assert result.evaluated, "the planner produced no candidates"
        assert any(plan.valid for plan in result.evaluated)
        assert result.pareto_plans
