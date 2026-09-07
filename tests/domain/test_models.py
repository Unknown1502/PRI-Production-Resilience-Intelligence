"""Unit tests for src/pri/domain/models.py.

Coverage:
- All model constructors accept valid data.
- Validation guards raise ValueError on invalid data.
- Every model is immutable (frozen).
- ProductionState.apply() never mutates the input state.
- ProductionState.apply() correctly increments the version chain.
- Each Move kind applies its change correctly and independently.
- Discriminated union round-trips via model_validate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from pri.domain.models import (
    CandidatePlan,
    ConstraintViolation,
    DisruptionEvent,
    Equipment,
    EvaluatedPlan,
    Location,
    Move,
    MoveSceneToDay,
    Person,
    PlanScore,
    Production,
    ProductionState,
    RelocateScene,
    Scene,
    Schedule,
    ShiftCallTime,
    ShootingDay,
    SwapDays,
    TimeWindow,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UTC = UTC

D1 = date(2025, 3, 10)
D2 = date(2025, 3, 11)
D3 = date(2025, 3, 12)

T_CALL_D1 = datetime(2025, 3, 10, 7, 0, tzinfo=UTC)
T_WRAP_D1 = datetime(2025, 3, 10, 19, 0, tzinfo=UTC)
T_CALL_D2 = datetime(2025, 3, 11, 7, 0, tzinfo=UTC)
T_WRAP_D2 = datetime(2025, 3, 11, 19, 0, tzinfo=UTC)


def _production() -> Production:
    return Production(
        id="prod-1",
        title="Test Film",
        currency="USD",
        shoot_start=D1,
        shoot_end=D3,
        reserve_days=(),
    )


def _scene(
    scene_id: str = "sc-1",
    location_id: str = "loc-1",
    number: int = 1,
) -> Scene:
    return Scene(
        id=scene_id,
        number=number,
        slug=f"sc{number:03d}",
        description="A test scene",
        int_ext="INT",
        time_of_day="DAY",
        estimated_minutes=30,
        location_id=location_id,
        cast_ids=("person-1",),
        equipment_ids=("equip-1",),
        vfx_plate=False,
        prerequisite_scene_ids=(),
    )


def _person() -> Person:
    return Person(
        id="person-1",
        name="Jane Actor",
        role="CAST",
        character="Hero",
        daily_rate=Decimal("5000.00"),
        unavailable_windows=(),
    )


def _location(location_id: str = "loc-1") -> Location:
    return Location(
        id=location_id,
        name="Studio A",
        kind="studio",
        day_rate=Decimal("2000.00"),
        permit_windows=(),
        supports_int_ext=("INT",),
        supports_time_of_day=("DAY",),
    )


def _equipment() -> Equipment:
    return Equipment(
        id="equip-1",
        name="Arri Alexa",
        kind="camera",
        daily_rate=Decimal("800.00"),
        available_windows=(),
    )


def _shooting_day(
    shoot_date: date = D1,
    scene_ids: tuple[str, ...] = ("sc-1",),
    location_id: str = "loc-1",
    call_time: datetime | None = None,
    wrap_time: datetime | None = None,
) -> ShootingDay:
    ct = call_time or datetime(shoot_date.year, shoot_date.month, shoot_date.day, 7, 0, tzinfo=UTC)
    wt = wrap_time or datetime(shoot_date.year, shoot_date.month, shoot_date.day, 19, 0, tzinfo=UTC)
    return ShootingDay(
        date=shoot_date,
        call_time=ct,
        wrap_time=wt,
        location_id=location_id,
        scene_ids=scene_ids,
    )


def _state(extra_scenes: tuple[Scene, ...] = ()) -> ProductionState:
    sc1 = _scene("sc-1", "loc-1", 1)
    sc2 = _scene("sc-2", "loc-1", 2)
    all_scenes = (sc1, sc2, *extra_scenes)
    day1 = _shooting_day(D1, scene_ids=("sc-1",))
    day2 = _shooting_day(D2, scene_ids=("sc-2",))
    return ProductionState(
        production=_production(),
        scenes=all_scenes,
        people=(_person(),),
        locations=(_location(),),
        equipment=(_equipment(),),
        schedule=Schedule(days=(day1, day2)),
        version=1,
        parent_version=None,
        event_id=None,
        created_at=datetime(2025, 3, 1, 0, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# TimeWindow
# ---------------------------------------------------------------------------


class TestTimeWindow:
    def test_valid(self) -> None:
        tw = TimeWindow(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 1, 2, tzinfo=UTC),
        )
        assert tw.end > tw.start

    def test_end_equals_start_raises(self) -> None:
        t = datetime(2025, 1, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="end"):
            TimeWindow(start=t, end=t)

    def test_end_before_start_raises(self) -> None:
        with pytest.raises(ValueError):
            TimeWindow(
                start=datetime(2025, 1, 2, tzinfo=UTC),
                end=datetime(2025, 1, 1, tzinfo=UTC),
            )

    def test_immutable(self) -> None:
        tw = TimeWindow(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 1, 2, tzinfo=UTC),
        )
        with pytest.raises(ValidationError):
            tw.start = datetime(2025, 6, 1, tzinfo=UTC)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------------


class TestProduction:
    def test_valid(self) -> None:
        p = _production()
        assert p.title == "Test Film"

    def test_same_start_end_ok(self) -> None:
        p = Production(
            id="p",
            title="T",
            currency="GBP",
            shoot_start=D1,
            shoot_end=D1,
            reserve_days=(),
        )
        assert p.shoot_start == p.shoot_end

    def test_end_before_start_raises(self) -> None:
        with pytest.raises(ValueError, match="shoot_end"):
            Production(
                id="p",
                title="T",
                currency="GBP",
                shoot_start=D2,
                shoot_end=D1,
                reserve_days=(),
            )

    def test_immutable(self) -> None:
        p = _production()
        with pytest.raises(ValidationError):
            p.title = "Mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------


class TestScene:
    def test_valid(self) -> None:
        s = _scene()
        assert s.estimated_minutes == 30

    def test_zero_duration_raises(self) -> None:
        with pytest.raises(ValueError, match="estimated_minutes"):
            Scene(
                id="s",
                number=1,
                slug="sc001",
                description="",
                int_ext="INT",
                time_of_day="DAY",
                estimated_minutes=0,
                location_id="loc-1",
                cast_ids=(),
                equipment_ids=(),
                vfx_plate=False,
                prerequisite_scene_ids=(),
            )

    def test_negative_duration_raises(self) -> None:
        with pytest.raises(ValueError):
            Scene(
                id="s",
                number=1,
                slug="sc001",
                description="",
                int_ext="EXT",
                time_of_day="NIGHT",
                estimated_minutes=-5,
                location_id="loc-1",
                cast_ids=(),
                equipment_ids=(),
                vfx_plate=True,
                prerequisite_scene_ids=(),
            )


# ---------------------------------------------------------------------------
# Person / Location / Equipment — shared rate guard
# ---------------------------------------------------------------------------


class TestEntityRates:
    def test_person_negative_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="daily_rate"):
            Person(
                id="p",
                name="X",
                role="CREW",
                character=None,
                daily_rate=Decimal("-1"),
                unavailable_windows=(),
            )

    def test_location_negative_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="day_rate"):
            Location(
                id="l",
                name="X",
                kind="ext",
                day_rate=Decimal("-0.01"),
                permit_windows=(),
                supports_int_ext=(),
                supports_time_of_day=(),
            )

    def test_equipment_negative_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="daily_rate"):
            Equipment(
                id="e",
                name="X",
                kind="grip",
                daily_rate=Decimal("-100"),
                available_windows=(),
            )

    def test_zero_rates_ok(self) -> None:
        p = Person(
            id="p",
            name="X",
            role="CREW",
            character=None,
            daily_rate=Decimal("0"),
            unavailable_windows=(),
        )
        assert p.daily_rate == Decimal("0")


# ---------------------------------------------------------------------------
# ShootingDay
# ---------------------------------------------------------------------------


class TestShootingDay:
    def test_valid(self) -> None:
        d = _shooting_day()
        assert d.unit == "MAIN"

    def test_wrap_before_call_raises(self) -> None:
        with pytest.raises(ValueError, match="wrap_time"):
            ShootingDay(
                date=D1,
                call_time=T_CALL_D1,
                wrap_time=T_CALL_D1 - timedelta(hours=1),
                location_id="loc-1",
                scene_ids=(),
            )

    def test_wrap_equals_call_raises(self) -> None:
        with pytest.raises(ValueError):
            ShootingDay(
                date=D1,
                call_time=T_CALL_D1,
                wrap_time=T_CALL_D1,
                location_id="loc-1",
                scene_ids=(),
            )


# ---------------------------------------------------------------------------
# ProductionState — construction
# ---------------------------------------------------------------------------


class TestProductionStateConstruction:
    def test_version_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="version"):
            ProductionState(
                production=_production(),
                scenes=(),
                people=(),
                locations=(),
                equipment=(),
                schedule=Schedule(days=()),
                version=0,
                parent_version=None,
                event_id=None,
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
            )

    def test_parent_version_gte_version_raises(self) -> None:
        with pytest.raises(ValueError, match="parent_version"):
            ProductionState(
                production=_production(),
                scenes=(),
                people=(),
                locations=(),
                equipment=(),
                schedule=Schedule(days=()),
                version=2,
                parent_version=2,
                event_id=None,
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
            )

    def test_parent_version_less_than_version_ok(self) -> None:
        s = ProductionState(
            production=_production(),
            scenes=(),
            people=(),
            locations=(),
            equipment=(),
            schedule=Schedule(days=()),
            version=3,
            parent_version=2,
            event_id=None,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert s.version == 3
        assert s.parent_version == 2

    def test_immutable(self) -> None:
        s = _state()
        with pytest.raises(ValidationError):
            s.version = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProductionState.apply() — version chain
# ---------------------------------------------------------------------------


class TestApplyVersionChain:
    def test_apply_empty_increments_version(self) -> None:
        original = _state()
        new_state = original.apply([])
        assert new_state.version == 2
        assert new_state.parent_version == 1

    def test_apply_does_not_mutate_input(self) -> None:
        original = _state()
        _ = original.apply([])
        assert original.version == 1
        assert original.parent_version is None

    def test_chained_apply_builds_lineage(self) -> None:
        s0 = _state()
        s1 = s0.apply([])
        s2 = s1.apply([])
        assert s0.version == 1
        assert s1.version == 2
        assert s1.parent_version == 1
        assert s2.version == 3
        assert s2.parent_version == 2

    def test_apply_sets_event_id_to_none(self) -> None:
        # Build a state with an event_id set.
        s = _state().model_copy(update={"event_id": "evt-abc"})
        new_state = s.apply([])
        assert new_state.event_id is None


# ---------------------------------------------------------------------------
# ProductionState.apply() — MoveSceneToDay
# ---------------------------------------------------------------------------


class TestApplyMoveSceneToDay:
    def test_moves_scene_between_days(self) -> None:
        state = _state()
        # sc-1 is on D1; move it to D2.
        move = MoveSceneToDay(scene_id="sc-1", target_date=D2)
        new_state = state.apply([move])

        days = {d.date: d for d in new_state.schedule.days}
        assert "sc-1" not in days[D1].scene_ids
        assert "sc-1" in days[D2].scene_ids

    def test_source_day_retains_remaining_scenes(self) -> None:
        # Build state where D1 has TWO scenes.
        sc3 = _scene("sc-3", "loc-1", 3)
        d1 = _shooting_day(D1, scene_ids=("sc-1", "sc-3"))
        d2 = _shooting_day(D2, scene_ids=("sc-2",))
        sc1 = _scene("sc-1")
        sc2 = _scene("sc-2")
        state = ProductionState(
            production=_production(),
            scenes=(sc1, sc2, sc3),
            people=(_person(),),
            locations=(_location(),),
            equipment=(_equipment(),),
            schedule=Schedule(days=(d1, d2)),
            version=1,
            parent_version=None,
            event_id=None,
            created_at=datetime(2025, 3, 1, tzinfo=UTC),
        )
        move = MoveSceneToDay(scene_id="sc-1", target_date=D2)
        new_state = state.apply([move])
        days = {d.date: d for d in new_state.schedule.days}
        assert days[D1].scene_ids == ("sc-3",)
        assert "sc-1" in days[D2].scene_ids

    def test_missing_target_date_raises(self) -> None:
        state = _state()
        move = MoveSceneToDay(scene_id="sc-1", target_date=date(2099, 1, 1))
        with pytest.raises(KeyError):
            state.apply([move])

    def test_missing_scene_id_raises(self) -> None:
        state = _state()
        move = MoveSceneToDay(scene_id="nonexistent", target_date=D2)
        with pytest.raises(ValueError):
            state.apply([move])

    def test_input_state_unchanged(self) -> None:
        state = _state()
        days_before = {d.date: d.scene_ids for d in state.schedule.days}
        _ = state.apply([MoveSceneToDay(scene_id="sc-1", target_date=D2)])
        days_after = {d.date: d.scene_ids for d in state.schedule.days}
        assert days_before == days_after


# ---------------------------------------------------------------------------
# ProductionState.apply() — SwapDays
# ---------------------------------------------------------------------------


class TestApplySwapDays:
    def test_swaps_scene_lists(self) -> None:
        state = _state()
        move = SwapDays(date_a=D1, date_b=D2)
        new_state = state.apply([move])
        days = {d.date: d for d in new_state.schedule.days}
        assert days[D1].scene_ids == ("sc-2",)
        assert days[D2].scene_ids == ("sc-1",)

    def test_original_state_unchanged(self) -> None:
        state = _state()
        _ = state.apply([SwapDays(date_a=D1, date_b=D2)])
        days = {d.date: d for d in state.schedule.days}
        assert days[D1].scene_ids == ("sc-1",)
        assert days[D2].scene_ids == ("sc-2",)

    def test_missing_date_a_raises(self) -> None:
        state = _state()
        with pytest.raises(KeyError):
            state.apply([SwapDays(date_a=date(2099, 1, 1), date_b=D2)])

    def test_missing_date_b_raises(self) -> None:
        state = _state()
        with pytest.raises(KeyError):
            state.apply([SwapDays(date_a=D1, date_b=date(2099, 1, 1))])


# ---------------------------------------------------------------------------
# ProductionState.apply() — ShiftCallTime
# ---------------------------------------------------------------------------


class TestApplyShiftCallTime:
    def test_shifts_call_and_holds_wrap_fixed(self) -> None:
        """A later call buys turnaround by shortening the day, not by moving wrap.

        Wrap is pinned by things a schedule change cannot negotiate — a permit
        expiring, the light going — so shifting it too would swap a turnaround
        violation for a permit violation.
        """
        state = _state()
        original_day = state.schedule.days[0]
        original_duration = original_day.wrap_time - original_day.call_time

        new_call = original_day.call_time + timedelta(hours=2)
        move = ShiftCallTime(date=D1, new_call_time=new_call)
        new_state = state.apply([move])

        days = {d.date: d for d in new_state.schedule.days}
        updated = days[D1]
        assert updated.call_time == new_call
        assert updated.wrap_time == original_day.wrap_time
        assert updated.wrap_time - updated.call_time == original_duration - timedelta(hours=2)

    def test_missing_date_raises(self) -> None:
        state = _state()
        with pytest.raises(KeyError):
            state.apply([ShiftCallTime(date=date(2099, 1, 1), new_call_time=T_CALL_D1)])

    def test_original_call_time_unchanged(self) -> None:
        state = _state()
        original_call = state.schedule.days[0].call_time
        _ = state.apply([ShiftCallTime(date=D1, new_call_time=T_CALL_D1 + timedelta(hours=1))])
        assert state.schedule.days[0].call_time == original_call


# ---------------------------------------------------------------------------
# ProductionState.apply() — RelocateScene
# ---------------------------------------------------------------------------


class TestApplyRelocateScene:
    def test_changes_scene_location(self) -> None:
        # Add a second location to the state.
        loc2 = _location("loc-2")
        sc1 = _scene("sc-1", "loc-1")
        sc2 = _scene("sc-2", "loc-1")
        state = ProductionState(
            production=_production(),
            scenes=(sc1, sc2),
            people=(_person(),),
            locations=(_location("loc-1"), loc2),
            equipment=(_equipment(),),
            schedule=Schedule(days=(_shooting_day(D1), _shooting_day(D2, scene_ids=("sc-2",)))),
            version=1,
            parent_version=None,
            event_id=None,
            created_at=datetime(2025, 3, 1, tzinfo=UTC),
        )
        move = RelocateScene(scene_id="sc-1", target_location_id="loc-2")
        new_state = state.apply([move])

        scenes_by_id = {s.id: s for s in new_state.scenes}
        assert scenes_by_id["sc-1"].location_id == "loc-2"
        assert scenes_by_id["sc-2"].location_id == "loc-1"

    def test_original_location_unchanged(self) -> None:
        state = _state()
        _ = state.apply([RelocateScene(scene_id="sc-1", target_location_id="loc-99")])
        scenes_by_id = {s.id: s for s in state.scenes}
        assert scenes_by_id["sc-1"].location_id == "loc-1"

    def test_missing_scene_raises(self) -> None:
        state = _state()
        with pytest.raises(KeyError):
            state.apply([RelocateScene(scene_id="nonexistent", target_location_id="loc-1")])


# ---------------------------------------------------------------------------
# Compound apply — multiple moves in one call
# ---------------------------------------------------------------------------


class TestApplyCompound:
    def test_swap_then_relocate(self) -> None:
        loc2 = _location("loc-2")
        sc1 = _scene("sc-1", "loc-1")
        sc2 = _scene("sc-2", "loc-1")
        state = ProductionState(
            production=_production(),
            scenes=(sc1, sc2),
            people=(_person(),),
            locations=(_location("loc-1"), loc2),
            equipment=(_equipment(),),
            schedule=Schedule(days=(_shooting_day(D1), _shooting_day(D2, scene_ids=("sc-2",)))),
            version=1,
            parent_version=None,
            event_id=None,
            created_at=datetime(2025, 3, 1, tzinfo=UTC),
        )
        moves: list[Move] = [
            SwapDays(date_a=D1, date_b=D2),
            RelocateScene(scene_id="sc-1", target_location_id="loc-2"),
        ]
        new_state = state.apply(moves)
        days = {d.date: d for d in new_state.schedule.days}
        assert days[D1].scene_ids == ("sc-2",)
        assert days[D2].scene_ids == ("sc-1",)
        scenes_by_id = {s.id: s for s in new_state.scenes}
        assert scenes_by_id["sc-1"].location_id == "loc-2"

    def test_version_incremented_once_for_many_moves(self) -> None:
        state = _state()
        moves: list[Move] = [
            MoveSceneToDay(scene_id="sc-1", target_date=D2),
            ShiftCallTime(date=D2, new_call_time=T_CALL_D2 + timedelta(hours=1)),
        ]
        new_state = state.apply(moves)
        assert new_state.version == state.version + 1


# ---------------------------------------------------------------------------
# DisruptionEvent
# ---------------------------------------------------------------------------


class TestDisruptionEvent:
    def test_valid(self) -> None:
        ev = DisruptionEvent(
            event_id="evt-1",
            production_id="prod-1",
            event_type="location.blocked",
            occurred_at=datetime(2025, 3, 10, 8, 0, tzinfo=UTC),
            source="location manager report",
            severity=0.8,
            payload={"location_id": "loc-1", "reason": "flooding"},
        )
        assert ev.event_type == "location.blocked"

    def test_severity_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match="severity"):
            DisruptionEvent(
                event_id="evt-2",
                production_id="prod-1",
                event_type="weather.changed",
                occurred_at=datetime(2025, 3, 10, tzinfo=UTC),
                source="weather API",
                severity=1.1,
                payload={},
            )

    def test_severity_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            DisruptionEvent(
                event_id="evt-3",
                production_id="prod-1",
                event_type="equipment.failed",
                occurred_at=datetime(2025, 3, 10, tzinfo=UTC),
                source="onset report",
                severity=-0.1,
                payload={},
            )

    def test_immutable(self) -> None:
        ev = DisruptionEvent(
            event_id="evt-4",
            production_id="prod-1",
            event_type="actor.unavailable",
            occurred_at=datetime(2025, 3, 10, tzinfo=UTC),
            source="casting",
            severity=0.5,
            payload={},
        )
        with pytest.raises(ValidationError):
            ev.severity = 0.1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Move discriminated union
# ---------------------------------------------------------------------------


class TestMoveDiscriminatedUnion:
    def test_move_scene_to_day_round_trip(self) -> None:
        from pydantic import TypeAdapter

        ta: TypeAdapter[Move] = TypeAdapter(Move)
        raw = {"kind": "MoveSceneToDay", "scene_id": "sc-1", "target_date": "2025-03-11"}
        m = ta.validate_python(raw)
        assert isinstance(m, MoveSceneToDay)
        assert m.scene_id == "sc-1"

    def test_swap_days_round_trip(self) -> None:
        from pydantic import TypeAdapter

        ta: TypeAdapter[Move] = TypeAdapter(Move)
        raw = {"kind": "SwapDays", "date_a": "2025-03-10", "date_b": "2025-03-11"}
        m = ta.validate_python(raw)
        assert isinstance(m, SwapDays)

    def test_shift_call_time_round_trip(self) -> None:
        from pydantic import TypeAdapter

        ta: TypeAdapter[Move] = TypeAdapter(Move)
        raw = {
            "kind": "ShiftCallTime",
            "date": "2025-03-10",
            "new_call_time": "2025-03-10T09:00:00+00:00",
        }
        m = ta.validate_python(raw)
        assert isinstance(m, ShiftCallTime)

    def test_relocate_scene_round_trip(self) -> None:
        from pydantic import TypeAdapter

        ta: TypeAdapter[Move] = TypeAdapter(Move)
        raw = {"kind": "RelocateScene", "scene_id": "sc-1", "target_location_id": "loc-2"}
        m = ta.validate_python(raw)
        assert isinstance(m, RelocateScene)

    def test_unknown_kind_raises(self) -> None:
        from pydantic import TypeAdapter, ValidationError

        ta: TypeAdapter[Move] = TypeAdapter(Move)
        with pytest.raises(ValidationError):
            ta.validate_python({"kind": "DeleteEverything", "scene_id": "sc-1"})


# ---------------------------------------------------------------------------
# CandidatePlan / ConstraintViolation / PlanScore / EvaluatedPlan
# ---------------------------------------------------------------------------


class TestPlanModels:
    def test_candidate_plan_immutable(self) -> None:
        cp = CandidatePlan(
            id="plan-1",
            label="Option A",
            base_version=1,
            moves=(MoveSceneToDay(scene_id="sc-1", target_date=D2),),
            rationale_hint="Move interior to cover rain",
        )
        with pytest.raises(ValidationError):
            cp.label = "Mutated"  # type: ignore[misc]

    def test_constraint_violation_fields(self) -> None:
        cv = ConstraintViolation(
            code="LOC_UNAVAILABLE",
            severity="HARD",
            message="Location closed on that date",
            subject_ids=("loc-1",),
            observed="2025-03-10",
            required="closed",
        )
        assert cv.severity == "HARD"

    def test_plan_score_all_fields(self) -> None:
        ps = PlanScore(
            schedule_delay_days=1.0,
            incremental_cost=Decimal("12500.00"),
            operational_risk=0.3,
            affected_scene_count=4,
            crew_disruption_hours=8.0,
            downstream_dependency_impact=2,
        )
        assert ps.incremental_cost == Decimal("12500.00")

    def test_evaluated_plan_defaults(self) -> None:
        cp = CandidatePlan(
            id="plan-2",
            label="Option B",
            base_version=1,
            moves=(),
            rationale_hint=None,
        )
        ep = EvaluatedPlan(
            plan=cp,
            valid=True,
            violations=(),
            score=None,
            resulting_state_digest=None,
        )
        assert ep.pareto_optimal is False
        assert ep.valid is True
