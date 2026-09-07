"""Integration tests for PriRepository against a real PostgreSQL instance.

Run with a live Postgres (docker-compose.dev.yml):

    docker-compose -f docker-compose.dev.yml up -d
    pytest tests/persistence/ -v

Tests marked ``@pytest.mark.integration`` are skipped automatically when
``TEST_DATABASE_URL`` is not set and the default host is unreachable.  The
conftest handles schema creation/teardown per-test.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from pri.domain.models import (
    CandidatePlan,
    DisruptionEvent,
    Equipment,
    EvaluatedPlan,
    Location,
    Person,
    PlanScore,
    Production,
    ProductionState,
    Scene,
    Schedule,
    ShootingDay,
)

if TYPE_CHECKING:
    from pri.persistence.database import SessionFactory

from pri.persistence.errors import (
    ProductionNotFoundError,
    StaleStateError,
    StateVersionNotFoundError,
)
from pri.persistence.repository import PriRepository
from pri.persistence.serialization import compute_digest, state_to_snapshot

# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

D1 = date(2025, 4, 1)
D2 = date(2025, 4, 2)
UTC_NOW = datetime(2025, 3, 1, 0, 0, tzinfo=UTC)


def _production(pid: str = "prod-1") -> Production:
    return Production(
        id=pid,
        title="Test Film",
        currency="USD",
        shoot_start=D1,
        shoot_end=D2,
        reserve_days=(),
    )


def _scene(sid: str = "sc-1") -> Scene:
    return Scene(
        id=sid,
        number=1,
        slug="sc001",
        description="",
        int_ext="INT",
        time_of_day="DAY",
        estimated_minutes=30,
        location_id="loc-1",
        cast_ids=(),
        equipment_ids=(),
        vfx_plate=False,
        prerequisite_scene_ids=(),
    )


def _person() -> Person:
    return Person(
        id="p-1",
        name="Jane",
        role="CAST",
        character="Hero",
        daily_rate=Decimal("5000"),
        unavailable_windows=(),
    )


def _location() -> Location:
    return Location(
        id="loc-1",
        name="Studio A",
        kind="studio",
        day_rate=Decimal("2000"),
        permit_windows=(),
        supports_int_ext=("INT",),
        supports_time_of_day=("DAY",),
    )


def _equipment() -> Equipment:
    return Equipment(
        id="eq-1",
        name="Camera",
        kind="camera",
        daily_rate=Decimal("800"),
        available_windows=(),
    )


def _shooting_day(d: date = D1) -> ShootingDay:
    return ShootingDay(
        date=d,
        call_time=datetime(d.year, d.month, d.day, 7, 0, tzinfo=UTC),
        wrap_time=datetime(d.year, d.month, d.day, 19, 0, tzinfo=UTC),
        location_id="loc-1",
        scene_ids=("sc-1",),
    )


def _root_state(pid: str = "prod-1") -> ProductionState:
    return ProductionState(
        production=_production(pid),
        scenes=(_scene(),),
        people=(_person(),),
        locations=(_location(),),
        equipment=(_equipment(),),
        schedule=Schedule(days=(_shooting_day(),)),
        version=1,
        parent_version=None,
        event_id=None,
        created_at=UTC_NOW,
    )


def _disruption(eid: str = "evt-1", pid: str = "prod-1") -> DisruptionEvent:
    return DisruptionEvent(
        event_id=eid,
        production_id=pid,
        event_type="location.blocked",
        occurred_at=datetime(2025, 3, 10, 8, 0, tzinfo=UTC),
        source="location manager",
        severity=0.7,
        payload={"location_id": "loc-1"},
    )


def _make_repo(factory: SessionFactory) -> PriRepository:
    return PriRepository(factory)


# ---------------------------------------------------------------------------
# Serialization unit tests (no DB needed)
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_round_trip(self) -> None:
        from pri.persistence.serialization import snapshot_to_state

        state = _root_state()
        json_str, _digest = state_to_snapshot(state)
        restored = snapshot_to_state(json_str)
        assert restored.version == state.version
        assert restored.production.title == state.production.title
        assert restored.scenes[0].id == state.scenes[0].id

    def test_digest_is_deterministic(self) -> None:
        state = _root_state()
        _, d1 = state_to_snapshot(state)
        _, d2 = state_to_snapshot(state)
        assert d1 == d2
        assert len(d1) == 64  # SHA-256 hex

    def test_digest_changes_on_mutation(self) -> None:
        s1 = _root_state()
        s2 = s1.model_copy(update={"version": 2, "parent_version": 1})
        _, d1 = state_to_snapshot(s1)
        _, d2 = state_to_snapshot(s2)
        assert d1 != d2

    def test_compute_digest_matches(self) -> None:
        state = _root_state()
        json_str, digest = state_to_snapshot(state)
        assert compute_digest(json_str) == digest


# ---------------------------------------------------------------------------
# Integration tests — require live Postgres
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCommitAndRetrieve:
    async def test_commit_root_state(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        state = _root_state()
        version = await repo.commit_state(state)
        assert version == 1

    async def test_get_current_state_after_commit(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        state = _root_state()
        await repo.commit_state(state)
        retrieved = await repo.get_current_state("prod-1")
        assert retrieved.version == 1
        assert retrieved.production.title == "Test Film"
        assert retrieved.scenes[0].id == "sc-1"

    async def test_get_specific_version(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        s1 = _root_state()
        await repo.commit_state(s1)
        s2 = s1.apply([])  # version=2, parent_version=1
        await repo.commit_state(s2)
        retrieved = await repo.get_state("prod-1", 1)
        assert retrieved.version == 1

    async def test_get_current_state_returns_latest(
        self, db_session_factory: SessionFactory
    ) -> None:
        repo = _make_repo(db_session_factory)
        s1 = _root_state()
        await repo.commit_state(s1)
        s2 = s1.apply([])
        await repo.commit_state(s2)
        current = await repo.get_current_state("prod-1")
        assert current.version == 2

    async def test_commit_second_version(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        s1 = _root_state()
        await repo.commit_state(s1)
        # Use empty apply to get a valid v2 (D2 is not in the schedule)
        s2_valid = s1.apply([])
        v = await repo.commit_state(s2_valid)
        assert v == 2


@pytest.mark.asyncio
class TestStaleStateRejection:
    """Optimistic concurrency: committing from an outdated parent must fail."""

    async def test_stale_root_rejected_when_head_exists(
        self, db_session_factory: SessionFactory
    ) -> None:
        repo = _make_repo(db_session_factory)
        s1 = _root_state()
        await repo.commit_state(s1)
        # Try to commit another root (parent_version=None) when head exists.
        s1_dup = _root_state()
        with pytest.raises(StaleStateError) as exc_info:
            await repo.commit_state(s1_dup)
        assert exc_info.value.production_id == "prod-1"

    async def test_stale_parent_rejected(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        s1 = _root_state()
        await repo.commit_state(s1)
        s2a = s1.apply([])  # version=2, parent=1
        s2b = s1.apply([])  # version=2, parent=1  (racing writer)
        # First succeeds.
        await repo.commit_state(s2a)
        # Second must fail: head is now 2, but s2b.parent_version=1.
        with pytest.raises(StaleStateError) as exc_info:
            await repo.commit_state(s2b)
        err = exc_info.value
        assert err.attempted_parent == 1
        assert err.actual_head == 2

    async def test_stale_error_carries_correct_versions(
        self, db_session_factory: SessionFactory
    ) -> None:
        repo = _make_repo(db_session_factory)
        s1 = _root_state()
        await repo.commit_state(s1)
        s2 = s1.apply([])
        await repo.commit_state(s2)
        # Now we have head=2.  Try to commit with parent=1 (stale by 1 generation).
        s3_stale = s1.apply([])  # parent=1
        with pytest.raises(StaleStateError) as exc_info:
            await repo.commit_state(s3_stale)
        assert exc_info.value.attempted_parent == 1
        assert exc_info.value.actual_head == 2

    async def test_correct_parent_succeeds(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        s1 = _root_state()
        await repo.commit_state(s1)
        s2 = s1.apply([])
        await repo.commit_state(s2)
        s3 = s2.apply([])  # parent=2  — correct
        v = await repo.commit_state(s3)
        assert v == 3


@pytest.mark.asyncio
class TestEventIdempotency:
    """record_event must be idempotent on duplicate event_id."""

    async def test_first_insert_returns_true(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        # Production row required for FK.
        s1 = _root_state()
        await repo.commit_state(s1)
        result = await repo.record_event(_disruption())
        assert result is True

    async def test_duplicate_insert_returns_false(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        s1 = _root_state()
        await repo.commit_state(s1)
        ev = _disruption()
        await repo.record_event(ev)
        result = await repo.record_event(ev)
        assert result is False

    async def test_different_event_ids_both_inserted(
        self, db_session_factory: SessionFactory
    ) -> None:
        repo = _make_repo(db_session_factory)
        s1 = _root_state()
        await repo.commit_state(s1)
        r1 = await repo.record_event(_disruption("evt-1"))
        r2 = await repo.record_event(_disruption("evt-2"))
        assert r1 is True
        assert r2 is True


@pytest.mark.asyncio
class TestRecoverySession:
    async def test_create_session_returns_uuid(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        await repo.commit_state(_root_state())
        sid = await repo.create_session("prod-1", None, base_version=1)
        assert len(sid) == 36  # UUID4 string

    async def test_add_candidates(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        await repo.commit_state(_root_state())
        sid = await repo.create_session("prod-1", None, 1)
        plan = CandidatePlan(
            id="plan-1",
            label="Option A",
            base_version=1,
            moves=(),
            rationale_hint=None,
        )
        ep = EvaluatedPlan(
            plan=plan,
            valid=True,
            violations=(),
            score=PlanScore(
                schedule_delay_days=0.0,
                incremental_cost=Decimal("0"),
                operational_risk=0.1,
                affected_scene_count=0,
                crew_disruption_hours=0.0,
                downstream_dependency_impact=0,
            ),
            resulting_state_digest="abc123",
            pareto_optimal=True,
        )
        # Should not raise.
        await repo.add_candidates(sid, [ep])

    async def test_two_sessions_keep_their_own_plans(
        self, db_session_factory: SessionFactory
    ) -> None:
        """Plan ids repeat, so the key must include the session.

        Every recovery produces a plan-A. When `id` alone was the primary key,
        the second session anywhere in the database collided with the first and
        `ON CONFLICT DO NOTHING` swallowed it — the audit log recorded four
        candidates while the table kept none of them. Found on the deployed
        instance, where the recovery screen rehydrated to one plan and an empty
        frontier for a session whose API response had all four.
        """
        repo = _make_repo(db_session_factory)
        await repo.commit_state(_root_state())

        def evaluated(plan_id: str, label: str) -> EvaluatedPlan:
            return EvaluatedPlan(
                plan=CandidatePlan(
                    id=plan_id, label=label, base_version=1, moves=(), rationale_hint=None
                ),
                valid=True,
                violations=(),
                score=None,
                resulting_state_digest=None,
            )

        first = await repo.create_session("prod-1", None, 1)
        second = await repo.create_session("prod-1", None, 1)

        plans = [evaluated("plan-A", "A"), evaluated("plan-B", "B")]
        await repo.add_candidates(first, plans)
        await repo.add_candidates(second, plans)

        assert len(await repo.get_candidates(first)) == 2
        assert len(await repo.get_candidates(second)) == 2, (
            "the second session lost its candidates to the first session's ids"
        )

    async def test_an_approval_binds_to_the_plan_in_its_own_session(
        self, db_session_factory: SessionFactory
    ) -> None:
        """The composite key has to carry through to approvals as well."""
        repo = _make_repo(db_session_factory)
        await repo.commit_state(_root_state())

        plan = EvaluatedPlan(
            plan=CandidatePlan(
                id="plan-A", label="A", base_version=1, moves=(), rationale_hint=None
            ),
            valid=True,
            violations=(),
            score=None,
            resulting_state_digest=None,
        )
        first = await repo.create_session("prod-1", None, 1)
        second = await repo.create_session("prod-1", None, 1)
        await repo.add_candidates(first, [plan])
        await repo.add_candidates(second, [plan])

        approval = await repo.record_approval(second, "plan-A", "director", "APPROVED")
        assert len(approval) == 36

    async def test_record_approval(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        await repo.commit_state(_root_state())
        sid = await repo.create_session("prod-1", None, 1)
        plan = CandidatePlan(
            id="plan-2",
            label="Option B",
            base_version=1,
            moves=(),
            rationale_hint=None,
        )
        ep = EvaluatedPlan(
            plan=plan,
            valid=True,
            violations=(),
            score=None,
            resulting_state_digest=None,
        )
        await repo.add_candidates(sid, [ep])
        approval_id = await repo.record_approval(sid, "plan-2", "director", "APPROVED")
        assert len(approval_id) == 36

    async def test_invalid_decision_raises(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        await repo.commit_state(_root_state())
        sid = await repo.create_session("prod-1", None, 1)
        with pytest.raises(ValueError, match="Invalid decision"):
            await repo.record_approval(sid, "plan-x", "director", "MAYBE")


@pytest.mark.asyncio
class TestAuditAndArtifacts:
    async def test_append_audit(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        await repo.commit_state(_root_state())
        # Should not raise.
        await repo.append_audit(
            "prod-1",
            actor="system",
            action="STATE_COMMITTED",
            subject="prod-1",
            detail={"version": 1},
        )

    async def test_record_artifact(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        await repo.commit_state(_root_state())
        artifact_id = await repo.record_artifact(
            "prod-1", version=1, kind="call_sheet_pdf", path="/reports/cs_1.pdf"
        )
        assert len(artifact_id) == 36


@pytest.mark.asyncio
class TestNotFoundErrors:
    async def test_get_current_state_missing_production(
        self, db_session_factory: SessionFactory
    ) -> None:
        repo = _make_repo(db_session_factory)
        with pytest.raises(ProductionNotFoundError):
            await repo.get_current_state("nonexistent")

    async def test_get_state_missing_version(self, db_session_factory: SessionFactory) -> None:
        repo = _make_repo(db_session_factory)
        await repo.commit_state(_root_state())
        with pytest.raises(StateVersionNotFoundError):
            await repo.get_state("prod-1", 999)
