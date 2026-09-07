"""The seven-step guarded execution path, against a real database.

Architecture law 4 says no consequential mutation may bypass the sequence. That
is a claim about what the code *cannot* do, so most of this file is about
failure: a stale head, a plan that no longer validates, an approval that was
never given, and a commit reached with the guards stubbed out.

Postgres is real here on purpose. The rollback guarantee is transactional, and
a fake repository would happily "roll back" a write it never made.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from pri.config import get_settings
from pri.domain.models import DisruptionEvent, MoveSceneToDay, ProductionState
from pri.engine.simulation.replan import recover
from pri.engine.transition import (
    STEPS,
    ApprovalRequiredError,
    AuthorizationError,
    ConstraintViolationError,
    TransitionService,
)
from pri.persistence.bootstrap import split_sql_statements
from pri.persistence.database import SessionFactory
from pri.persistence.errors import StaleStateError
from pri.persistence.repository import PriRepository
from pri.persistence.seed import build_state

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_DEFAULT_URL = "postgresql+asyncpg://pri_user:changeme@localhost:5432/pri"
#: Every migration, in filename order — the same schema the deploy builds.
_MIGRATIONS_DIR = Path(__file__).parents[2] / "src" / "pri" / "persistence" / "migrations"
_TABLES = """
    artifacts, audit_log, approvals, candidate_plans,
    recovery_sessions, state_versions, events, productions
"""

PRODUCTION = "film-001"
APPROVER = "producer@nighttrain"


@pytest_asyncio.fixture()
async def repo(tmp_path: Path) -> AsyncGenerator[PriRepository, None]:
    """A clean schema with the demo production committed as version 1."""
    get_settings.cache_clear()
    os.environ["PRI_ARTIFACT_ROOT"] = str(tmp_path / "artifacts")

    engine = create_async_engine(
        os.environ.get("TEST_DATABASE_URL", _DEFAULT_URL), echo=False, pool_pre_ping=False
    )
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLES} CASCADE"))
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            for statement in split_sql_statements(path.read_text()):
                await conn.execute(text(statement))

    factory = SessionFactory(engine)
    repository = PriRepository(factory)
    await repository.commit_state(build_state(), event_id=None)

    yield repository

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {_TABLES} CASCADE"))
    await engine.dispose()
    os.environ.pop("PRI_ARTIFACT_ROOT", None)
    get_settings.cache_clear()


@pytest_asyncio.fixture()
async def session(
    repo: PriRepository,
    night_train_state: ProductionState,
    loc04_blocked_event: DisruptionEvent,
) -> str:
    """A recovery session with A, B, C and B2 persisted, and nothing approved."""
    await repo.record_event(loc04_blocked_event)
    result = recover(night_train_state, loc04_blocked_event)
    await repo.create_session_with_id(
        result.session_id, PRODUCTION, loc04_blocked_event.event_id, result.base_version
    )
    await repo.add_candidates(result.session_id, list(result.evaluated))
    return result.session_id


@pytest.fixture()
def service(repo: PriRepository, tmp_path: Path) -> TransitionService:
    return TransitionService(repo, artifact_root=tmp_path / "artifacts")


async def _approve(repo: PriRepository, session_id: str, plan_id: str) -> None:
    await repo.record_approval(session_id, plan_id, APPROVER, "APPROVED", None)


# ---------------------------------------------------------------------------


class TestTransitionHappyPath:
    async def test_full_execution_produces_version_increment(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        await _approve(repo, session, "plan-B2")
        result = await service.execute(session, "plan-B2", APPROVER, "token")

        assert result.base_version == 1
        assert result.new_version == 2
        assert (await repo.get_current_state(PRODUCTION)).version == 2

    async def test_six_verification_checks_all_pass(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        await _approve(repo, session, "plan-B2")
        result = await service.execute(session, "plan-B2", APPROVER, "token")

        assert [c.code for c in result.verification.checks] == [
            "V1",
            "V2",
            "V3",
            "V4",
            "V5",
            "V6",
        ]
        assert result.verification.valid is True
        assert all(c.passed for c in result.verification.checks)

    async def test_every_step_is_audited_in_order(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        await _approve(repo, session, "plan-B2")
        result = await service.execute(session, "plan-B2", APPROVER, "token")

        assert result.audit_entries == STEPS
        actions = {entry["action"] for entry in await repo.get_audit(PRODUCTION)}
        for step in STEPS[:5]:
            assert f"transition.{step}" in actions
        assert "transition.completed" in actions

    async def test_call_sheets_are_regenerated(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        await _approve(repo, session, "plan-B2")
        result = await service.execute(session, "plan-B2", APPROVER, "token")

        assert result.artifacts
        registered = await repo.get_artifacts(PRODUCTION, result.new_version)
        assert {str(row["kind"]) for row in registered} == {"call_sheet"}


class TestTransitionGuards:
    async def test_stale_state_raises_before_commit(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        """Someone else advanced the head between planning and execution."""
        await _approve(repo, session, "plan-B2")
        head = await repo.get_current_state(PRODUCTION)
        await repo.commit_state(
            head.apply([MoveSceneToDay(scene_id="S21", target_date=date(2026, 9, 16))])
        )

        with pytest.raises(StaleStateError):
            await service.execute(session, "plan-B2", APPROVER, "token")

        assert (await repo.get_current_state(PRODUCTION)).version == 2

    async def test_hard_violation_raises_before_commit(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        """Plan B is the one the validator rejects; it must never execute."""
        await _approve(repo, session, "plan-B")

        with pytest.raises(ConstraintViolationError) as caught:
            await service.execute(session, "plan-B", APPROVER, "token")

        assert caught.value.step == "validate_constraints"
        assert "C001" in caught.value.codes
        assert (await repo.get_current_state(PRODUCTION)).version == 1

    async def test_missing_approval_raises_before_commit(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        with pytest.raises(ApprovalRequiredError) as caught:
            await service.execute(session, "plan-B2", APPROVER, "token")

        assert caught.value.step == "request_approval"
        assert (await repo.get_current_state(PRODUCTION)).version == 1

    async def test_approval_from_a_different_person_is_refused(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        await _approve(repo, session, "plan-B2")

        with pytest.raises(ApprovalRequiredError):
            await service.execute(session, "plan-B2", "someone-else", "token")

        assert (await repo.get_current_state(PRODUCTION)).version == 1

    async def test_unnamed_approver_is_refused(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        await _approve(repo, session, "plan-B2")

        with pytest.raises(AuthorizationError) as caught:
            await service.execute(session, "plan-B2", "   ", "token")

        assert caught.value.step == "verify_authorization"

    async def test_approver_not_on_the_permitted_list_is_refused(
        self, repo: PriRepository, session: str, tmp_path: Path
    ) -> None:
        restricted = TransitionService(
            repo,
            permitted_approvers=frozenset({"line.producer@nighttrain"}),
            artifact_root=tmp_path / "artifacts",
        )
        await _approve(repo, session, "plan-B2")

        with pytest.raises(AuthorizationError):
            await restricted.execute(session, "plan-B2", APPROVER, "token")

    async def test_cannot_reach_commit_without_passing_guards(
        self,
        repo: PriRepository,
        service: TransitionService,
        session: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Stub the commit and prove no unapproved path ever reaches it.

        If a future refactor reorders the sequence so the write happens before
        the approval check, this test fails loudly rather than shipping a
        system that writes first and asks later.
        """
        reached: list[str] = []

        async def _tripwire(*args: Any, **kwargs: Any) -> int:
            reached.append("commit_state")
            raise AssertionError("commit_state reached without passing the guards")

        monkeypatch.setattr(repo, "commit_state", _tripwire)

        # No approval recorded: step 5 must stop it.
        with pytest.raises(ApprovalRequiredError):
            await service.execute(session, "plan-B2", APPROVER, "token")
        assert reached == []

        # An invalid plan, fully approved: step 2 must stop it.
        await _approve(repo, session, "plan-B")
        with pytest.raises(ConstraintViolationError):
            await service.execute(session, "plan-B", APPROVER, "token")
        assert reached == []


class TestTransitionIdempotency:
    async def test_double_execute_returns_first_result(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        await _approve(repo, session, "plan-B2")
        first = await service.execute(session, "plan-B2", APPROVER, "token")
        second = await service.execute(session, "plan-B2", APPROVER, "token")

        assert second.replayed is True
        assert second.new_version == first.new_version
        assert second.base_version == first.base_version

    async def test_double_execute_writes_no_new_version(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        await _approve(repo, session, "plan-B2")
        await service.execute(session, "plan-B2", APPROVER, "token")
        await service.execute(session, "plan-B2", APPROVER, "token")

        assert (await repo.get_current_state(PRODUCTION)).version == 2

    async def test_replay_still_reports_the_six_checks(
        self, repo: PriRepository, service: TransitionService, session: str
    ) -> None:
        """ "We already did this" is not the same claim as "and it was correct"."""
        await _approve(repo, session, "plan-B2")
        await service.execute(session, "plan-B2", APPROVER, "token")
        replay = await service.execute(session, "plan-B2", APPROVER, "token")

        assert len(replay.verification.checks) == 6
        assert replay.verification.valid is True


class TestVerificationRollback:
    async def test_failed_verification_rolls_back_the_commit(
        self,
        repo: PriRepository,
        service: TransitionService,
        session: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 7 runs inside the write transaction, so a failure un-writes it.

        ``state_versions`` is append-only, so rollback cannot mean DELETE. The
        verification runs on the inserted row before the transaction commits;
        raising there aborts the whole write, and the version was never there.
        """
        from pri.engine.transition import VerificationFailedError
        from pri.engine.verification.verify import (
            CheckResult,
            VerificationReport,
        )

        def _always_fails(*args: Any, **kwargs: Any) -> VerificationReport:
            from datetime import UTC, datetime

            return VerificationReport(
                valid=False,
                checks=(
                    CheckResult(
                        code="V1",
                        name="no_hard_violations",
                        passed=False,
                        detail="Injected failure for the rollback test.",
                    ),
                ),
                at=datetime.now(UTC),
            )

        monkeypatch.setattr("pri.engine.transition.verify", _always_fails)
        await _approve(repo, session, "plan-B2")

        with pytest.raises(VerificationFailedError):
            await service.execute(session, "plan-B2", APPROVER, "token")

        assert (await repo.get_current_state(PRODUCTION)).version == 1

    async def test_failed_verification_is_recorded_for_human_review(
        self,
        repo: PriRepository,
        service: TransitionService,
        session: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from datetime import UTC, datetime

        from pri.engine.transition import VerificationFailedError
        from pri.engine.verification.verify import CheckResult, VerificationReport

        def _always_fails(*args: Any, **kwargs: Any) -> VerificationReport:
            return VerificationReport(
                valid=False,
                checks=(
                    CheckResult(
                        code="V2",
                        name="scene_set_intact",
                        passed=False,
                        detail="Injected failure for the rollback test.",
                    ),
                ),
                at=datetime.now(UTC),
            )

        monkeypatch.setattr("pri.engine.transition.verify", _always_fails)
        await _approve(repo, session, "plan-B2")

        with pytest.raises(VerificationFailedError):
            await service.execute(session, "plan-B2", APPROVER, "token")

        actions = {entry["action"] for entry in await repo.get_audit(PRODUCTION)}
        assert "transition.human_review" in actions
