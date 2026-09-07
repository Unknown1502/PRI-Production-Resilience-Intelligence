"""Unit tests for src/pri/engine/constraints/validator.py.

One focused test class per rule (C001-C010).
Each test builds the minimal state that triggers exactly one violation and
verifies code, severity, observed, and required strings.

Also includes the canonical swap test:
    Seed fixture: two consecutive days Sep 10 / Sep 11.
    After SwapDays(Sep 10, Sep 11) the crew turnaround becomes 9.0h
    (wrap Sep 10 21:00 → call Sep 11 06:00 = 9h).
    validate() must return exactly one C001 violation with observed="9.0h".
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from pri.domain.models import (
    Equipment,
    Location,
    Person,
    Production,
    ProductionState,
    Scene,
    Schedule,
    ShiftCallTime,
    ShootingDay,
    SwapDays,
    TimeWindow,
)
from pri.engine.constraints.validator import (
    RULE_CODES,
    RULES,
    validate,
)

# ---------------------------------------------------------------------------
# Shared policy override — uses default values from policies.yaml
# ---------------------------------------------------------------------------

_POLICY: dict[str, object] = {
    "crew_turnaround": {"minimum_hours": 10.0},
    "shooting": {"maximum_daily_hours": 12.0},
    "location": {"no_double_booking": True, "respect_permit_windows": True},
    "cast": {"unavailable_dates_are_hard": True},
    "equipment": {"no_double_booking": True, "respect_rental_windows": True},
    "dependencies": {"preserve_prerequisite_order": True},
    "scene_environment": {
        "enforce_int_ext_match": True,
        "enforce_time_of_day_match": True,
    },
    "approval": {"schedule_change": {"required": True}},
}


def _policy(**overrides: object) -> dict[str, object]:
    """Return a copy of _POLICY with top-level keys overridden."""
    p = dict(_POLICY)
    p.update(overrides)
    return p


# ---------------------------------------------------------------------------
# Minimal state builder helpers
# ---------------------------------------------------------------------------


def _prod() -> Production:
    return Production(
        id="p1",
        title="Test",
        currency="USD",
        shoot_start=date(2025, 9, 10),
        shoot_end=date(2025, 9, 12),
        reserve_days=(),
    )


def _scene(
    sid: str = "sc-1",
    loc_id: str = "loc-1",
    int_ext: str = "INT",
    time_of_day: str = "DAY",
    cast_ids: tuple[str, ...] = (),
    equipment_ids: tuple[str, ...] = (),
    prereq_ids: tuple[str, ...] = (),
) -> Scene:
    return Scene(
        id=sid,
        number=1,
        slug=sid,
        description="",
        int_ext=int_ext,  # type: ignore[arg-type]
        time_of_day=time_of_day,  # type: ignore[arg-type]
        estimated_minutes=30,
        location_id=loc_id,
        cast_ids=cast_ids,
        equipment_ids=equipment_ids,
        vfx_plate=False,
        prerequisite_scene_ids=prereq_ids,
    )


def _loc(
    lid: str = "loc-1",
    supports_int_ext: tuple[str, ...] = ("INT", "EXT"),
    supports_time_of_day: tuple[str, ...] = ("DAY", "NIGHT", "DAWN", "DUSK"),
    permit_windows: tuple[TimeWindow, ...] = (),
) -> Location:
    return Location(
        id=lid,
        name=lid,
        kind="studio",
        day_rate=Decimal("1000"),
        permit_windows=permit_windows,
        supports_int_ext=supports_int_ext,
        supports_time_of_day=supports_time_of_day,
    )


def _person(
    pid: str = "p-1",
    role: str = "CAST",
    unavailable: tuple[TimeWindow, ...] = (),
) -> Person:
    return Person(
        id=pid,
        name=pid,
        role=role,  # type: ignore[arg-type]
        character=None,
        daily_rate=Decimal("1000"),
        unavailable_windows=unavailable,
    )


def _equip(
    eid: str = "eq-1",
    available_windows: tuple[TimeWindow, ...] = (),
) -> Equipment:
    return Equipment(
        id=eid,
        name=eid,
        kind="camera",
        daily_rate=Decimal("500"),
        available_windows=available_windows,
    )


def _day(
    d: date,
    scene_ids: tuple[str, ...] = (),
    loc_id: str = "loc-1",
    call_hour: int = 7,
    wrap_hour: int = 19,
    unit: str = "MAIN",
) -> ShootingDay:
    return ShootingDay(
        date=d,
        call_time=datetime(d.year, d.month, d.day, call_hour, 0, tzinfo=UTC),
        wrap_time=datetime(d.year, d.month, d.day, wrap_hour, 0, tzinfo=UTC),
        location_id=loc_id,
        scene_ids=scene_ids,
        unit=unit,
    )


def _state(
    *,
    scenes: tuple[Scene, ...] = (),
    people: tuple[Person, ...] = (),
    locations: tuple[Location, ...] = (),
    equipment: tuple[Equipment, ...] = (),
    days: tuple[ShootingDay, ...] = (),
) -> ProductionState:
    return ProductionState(
        production=_prod(),
        scenes=scenes,
        people=people,
        locations=locations,
        equipment=equipment,
        schedule=Schedule(days=days),
        version=1,
        parent_version=None,
        event_id=None,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# C001 — crew_turnaround
# ---------------------------------------------------------------------------


class TestC001CrewTurnaround:
    """wrap[day-n] → call[day-n+1] must be >= minimum_hours (default 10h)."""

    D1 = date(2025, 9, 10)
    D2 = date(2025, 9, 11)

    def _violating_state(self) -> ProductionState:
        """Wrap 21:00 on D1, call 06:00 on D2 = 9h gap (< 10h)."""
        return _state(
            scenes=(_scene(),),
            locations=(_loc(),),
            days=(
                _day(self.D1, ("sc-1",), call_hour=9, wrap_hour=21),
                _day(self.D2, ("sc-1",), call_hour=6, wrap_hour=18),
            ),
        )

    def test_violation_raised(self) -> None:
        violations = validate(self._violating_state(), policy=_POLICY)
        c001 = [v for v in violations if v.code == "C001"]
        assert len(c001) == 1

    def test_observed_is_9h(self) -> None:
        violations = validate(self._violating_state(), policy=_POLICY)
        c001 = next(v for v in violations if v.code == "C001")
        assert c001.observed == "9.0h"

    def test_required_string(self) -> None:
        violations = validate(self._violating_state(), policy=_POLICY)
        c001 = next(v for v in violations if v.code == "C001")
        assert ">= 10.0h" in c001.required

    def test_severity_is_hard(self) -> None:
        violations = validate(self._violating_state(), policy=_POLICY)
        c001 = next(v for v in violations if v.code == "C001")
        assert c001.severity == "HARD"

    def test_no_violation_when_gap_exact(self) -> None:
        """Exactly 10h rest — no violation."""
        state = _state(
            scenes=(_scene(),),
            locations=(_loc(),),
            days=(
                _day(self.D1, ("sc-1",), call_hour=7, wrap_hour=20),
                _day(self.D2, ("sc-1",), call_hour=6, wrap_hour=18),
            ),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C001" for v in violations)

    def test_different_units_not_checked(self) -> None:
        """C001 only applies within the same unit."""
        state = _state(
            scenes=(_scene(),),
            locations=(_loc(),),
            days=(
                _day(self.D1, ("sc-1",), call_hour=9, wrap_hour=21, unit="MAIN"),
                _day(self.D2, ("sc-1",), call_hour=6, wrap_hour=18, unit="2ND"),
            ),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C001" for v in violations)


# ---------------------------------------------------------------------------
# C002 — max_daily_hours
# ---------------------------------------------------------------------------


class TestC002MaxDailyHours:
    D1 = date(2025, 9, 10)

    def test_violation_when_over_12h(self) -> None:
        state = _state(
            scenes=(_scene(),),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-1",), call_hour=6, wrap_hour=19),),  # 13h
        )
        violations = validate(state, policy=_POLICY)
        c002 = [v for v in violations if v.code == "C002"]
        assert len(c002) == 1
        assert c002[0].observed == "13.0h"
        assert "<= 12.0h" in c002[0].required

    def test_no_violation_exactly_12h(self) -> None:
        state = _state(
            scenes=(_scene(),),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-1",), call_hour=7, wrap_hour=19),),  # 12h
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C002" for v in violations)

    def test_severity_hard(self) -> None:
        state = _state(
            scenes=(_scene(),),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-1",), call_hour=5, wrap_hour=20),),  # 15h
        )
        violations = validate(state, policy=_POLICY)
        c002 = [v for v in violations if v.code == "C002"]
        assert c002[0].severity == "HARD"


# ---------------------------------------------------------------------------
# C003 — cast_availability
# ---------------------------------------------------------------------------


class TestC003CastAvailability:
    D1 = date(2025, 9, 10)

    def test_violation_when_cast_unavailable(self) -> None:
        """Person unavailable all day on D1; scene scheduled on D1."""
        window = TimeWindow(
            start=datetime(2025, 9, 10, 0, 0, tzinfo=UTC),
            end=datetime(2025, 9, 11, 0, 0, tzinfo=UTC),
        )
        person = _person("cast-1", unavailable=(window,))
        scene = _scene("sc-1", cast_ids=("cast-1",))
        state = _state(
            scenes=(scene,),
            people=(person,),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-1",)),),
        )
        violations = validate(state, policy=_POLICY)
        c003 = [v for v in violations if v.code == "C003"]
        assert len(c003) == 1
        assert "cast-1" in c003[0].subject_ids

    def test_no_violation_when_window_is_other_day(self) -> None:
        window = TimeWindow(
            start=datetime(2025, 9, 12, 0, 0, tzinfo=UTC),
            end=datetime(2025, 9, 13, 0, 0, tzinfo=UTC),
        )
        person = _person("cast-1", unavailable=(window,))
        scene = _scene("sc-1", cast_ids=("cast-1",))
        state = _state(
            scenes=(scene,),
            people=(person,),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-1",)),),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C003" for v in violations)

    def test_disabled_by_policy(self) -> None:
        window = TimeWindow(
            start=datetime(2025, 9, 10, 0, 0, tzinfo=UTC),
            end=datetime(2025, 9, 11, 0, 0, tzinfo=UTC),
        )
        person = _person("cast-1", unavailable=(window,))
        scene = _scene("sc-1", cast_ids=("cast-1",))
        state = _state(
            scenes=(scene,),
            people=(person,),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-1",)),),
        )
        pol = _policy(cast={"unavailable_dates_are_hard": False})
        violations = validate(state, policy=pol)
        assert not any(v.code == "C003" for v in violations)


# ---------------------------------------------------------------------------
# C004 — location_permit
# ---------------------------------------------------------------------------


class TestC004LocationPermit:
    D1 = date(2025, 9, 10)

    def test_violation_when_day_outside_permit(self) -> None:
        """Permit only allows 10:00-18:00; day runs 07:00-19:00."""
        permit = TimeWindow(
            start=datetime(2025, 9, 10, 10, 0, tzinfo=UTC),
            end=datetime(2025, 9, 10, 18, 0, tzinfo=UTC),
        )
        loc = _loc("loc-1", permit_windows=(permit,))
        state = _state(
            scenes=(_scene(),),
            locations=(loc,),
            days=(_day(self.D1, ("sc-1",), call_hour=7, wrap_hour=19),),
        )
        violations = validate(state, policy=_POLICY)
        c004 = [v for v in violations if v.code == "C004"]
        assert len(c004) == 1
        assert "loc-1" in c004[0].subject_ids

    def test_no_violation_when_day_inside_permit(self) -> None:
        permit = TimeWindow(
            start=datetime(2025, 9, 10, 6, 0, tzinfo=UTC),
            end=datetime(2025, 9, 10, 22, 0, tzinfo=UTC),
        )
        loc = _loc("loc-1", permit_windows=(permit,))
        state = _state(
            scenes=(_scene(),),
            locations=(loc,),
            days=(_day(self.D1, ("sc-1",), call_hour=7, wrap_hour=19),),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C004" for v in violations)

    def test_no_violation_when_no_permit_windows(self) -> None:
        """Empty permit_windows = no constraint."""
        loc = _loc("loc-1", permit_windows=())
        state = _state(
            scenes=(_scene(),),
            locations=(loc,),
            days=(_day(self.D1, ("sc-1",)),),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C004" for v in violations)


# ---------------------------------------------------------------------------
# C005 — location_double_book
# ---------------------------------------------------------------------------


class TestC005LocationDoubleBook:
    D1 = date(2025, 9, 10)

    def test_violation_two_units_same_location_same_day(self) -> None:
        state = _state(
            scenes=(_scene("sc-1"), _scene("sc-2")),
            locations=(_loc(),),
            days=(
                _day(self.D1, ("sc-1",), unit="MAIN"),
                _day(self.D1, ("sc-2",), unit="2ND"),
            ),
        )
        violations = validate(state, policy=_POLICY)
        c005 = [v for v in violations if v.code == "C005"]
        assert len(c005) == 1

    def test_no_violation_different_days(self) -> None:
        D2 = date(2025, 9, 11)
        state = _state(
            scenes=(_scene("sc-1"), _scene("sc-2")),
            locations=(_loc(),),
            days=(
                _day(self.D1, ("sc-1",), unit="MAIN"),
                _day(D2, ("sc-2",), unit="2ND"),
            ),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C005" for v in violations)

    def test_no_violation_different_locations(self) -> None:
        state = _state(
            scenes=(_scene("sc-1", loc_id="loc-1"), _scene("sc-2", loc_id="loc-2")),
            locations=(_loc("loc-1"), _loc("loc-2")),
            days=(
                _day(self.D1, ("sc-1",), loc_id="loc-1", unit="MAIN"),
                _day(self.D1, ("sc-2",), loc_id="loc-2", unit="2ND"),
            ),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C005" for v in violations)


# ---------------------------------------------------------------------------
# C006 — equipment_window
# ---------------------------------------------------------------------------


class TestC006EquipmentWindow:
    D1 = date(2025, 9, 10)

    def test_violation_when_outside_window(self) -> None:
        """Equipment only available Sep 15 onwards; used on Sep 10."""
        avail = TimeWindow(
            start=datetime(2025, 9, 15, 0, 0, tzinfo=UTC),
            end=datetime(2025, 9, 20, 0, 0, tzinfo=UTC),
        )
        equip = _equip("eq-1", available_windows=(avail,))
        scene = _scene("sc-1", equipment_ids=("eq-1",))
        state = _state(
            scenes=(scene,),
            equipment=(equip,),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-1",)),),
        )
        violations = validate(state, policy=_POLICY)
        c006 = [v for v in violations if v.code == "C006"]
        assert len(c006) == 1
        assert "eq-1" in c006[0].subject_ids

    def test_no_violation_when_inside_window(self) -> None:
        avail = TimeWindow(
            start=datetime(2025, 9, 10, 0, 0, tzinfo=UTC),
            end=datetime(2025, 9, 11, 0, 0, tzinfo=UTC),
        )
        equip = _equip("eq-1", available_windows=(avail,))
        scene = _scene("sc-1", equipment_ids=("eq-1",))
        state = _state(
            scenes=(scene,),
            equipment=(equip,),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-1",), call_hour=7, wrap_hour=19),),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C006" for v in violations)

    def test_no_violation_when_no_windows_defined(self) -> None:
        equip = _equip("eq-1", available_windows=())
        scene = _scene("sc-1", equipment_ids=("eq-1",))
        state = _state(
            scenes=(scene,),
            equipment=(equip,),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-1",)),),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C006" for v in violations)


# ---------------------------------------------------------------------------
# C007 — equipment_double_book
# ---------------------------------------------------------------------------


class TestC007EquipmentDoubleBook:
    D1 = date(2025, 9, 10)

    def test_violation_same_equip_two_units_same_day(self) -> None:
        equip = _equip("eq-1")
        sc1 = _scene("sc-1", equipment_ids=("eq-1",))
        sc2 = _scene("sc-2", equipment_ids=("eq-1",))
        state = _state(
            scenes=(sc1, sc2),
            equipment=(equip,),
            locations=(_loc(),),
            days=(
                _day(self.D1, ("sc-1",), unit="MAIN"),
                _day(self.D1, ("sc-2",), unit="2ND"),
            ),
        )
        violations = validate(state, policy=_POLICY)
        c007 = [v for v in violations if v.code == "C007"]
        assert len(c007) == 1
        assert "eq-1" in c007[0].subject_ids

    def test_no_violation_different_days(self) -> None:
        D2 = date(2025, 9, 11)
        equip = _equip("eq-1")
        sc1 = _scene("sc-1", equipment_ids=("eq-1",))
        sc2 = _scene("sc-2", equipment_ids=("eq-1",))
        state = _state(
            scenes=(sc1, sc2),
            equipment=(equip,),
            locations=(_loc(),),
            days=(
                _day(self.D1, ("sc-1",), unit="MAIN"),
                _day(D2, ("sc-2",), unit="2ND"),
            ),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C007" for v in violations)


# ---------------------------------------------------------------------------
# C008 — prerequisite_order
# ---------------------------------------------------------------------------


class TestC008PrerequisiteOrder:
    D1 = date(2025, 9, 10)
    D2 = date(2025, 9, 11)

    def test_violation_when_prereq_is_on_the_same_day(self) -> None:
        """Strictly earlier means a different date, however the shot list reads.

        A dependency that survives only because of the order two slugs happen to
        sit in is not a dependency the production can rely on: the running order
        changes on the morning.
        """
        sc1 = _scene("sc-1")
        sc2 = _scene("sc-2", prereq_ids=("sc-1",))
        state = _state(
            scenes=(sc1, sc2),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-1", "sc-2")),),
        )
        violations = validate(state, policy=_POLICY)
        c008 = [v for v in violations if v.code == "C008"]
        assert len(c008) == 1
        assert "sc-2" in c008[0].subject_ids
        assert "sc-1" in c008[0].subject_ids

    def test_same_day_violation_reads_well(self) -> None:
        sc1 = _scene("sc-1")
        sc2 = _scene("sc-2", prereq_ids=("sc-1",))
        state = _state(
            scenes=(sc1, sc2),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-1", "sc-2")),),
        )
        violation = next(v for v in validate(state, policy=_POLICY) if v.code == "C008")
        assert violation.observed == "sc-2 on 2025-09-10, sc-1 also on 2025-09-10"
        assert violation.required == "sc-1 on earlier date"

    def test_shot_list_order_does_not_rescue_a_same_day_pair(self) -> None:
        sc1 = _scene("sc-1")
        sc2 = _scene("sc-2", prereq_ids=("sc-1",))
        state = _state(
            scenes=(sc1, sc2),
            locations=(_loc(),),
            days=(_day(self.D1, ("sc-2", "sc-1")),),
        )
        assert any(v.code == "C008" for v in validate(state, policy=_POLICY))

    def test_violation_when_prereq_after(self) -> None:
        sc1 = _scene("sc-1")
        sc2 = _scene("sc-2", prereq_ids=("sc-1",))
        state = _state(
            scenes=(sc1, sc2),
            locations=(_loc(),),
            days=(
                _day(self.D1, ("sc-2",)),  # sc-2 before sc-1
                _day(self.D2, ("sc-1",)),
            ),
        )
        violations = validate(state, policy=_POLICY)
        c008 = [v for v in violations if v.code == "C008"]
        assert len(c008) == 1

    def test_no_violation_when_prereq_is_earlier(self) -> None:
        sc1 = _scene("sc-1")
        sc2 = _scene("sc-2", prereq_ids=("sc-1",))
        state = _state(
            scenes=(sc1, sc2),
            locations=(_loc(),),
            days=(
                _day(self.D1, ("sc-1",)),
                _day(self.D2, ("sc-2",)),
            ),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C008" for v in violations)


# ---------------------------------------------------------------------------
# C009 — int_ext_match
# ---------------------------------------------------------------------------


class TestC009IntExtMatch:
    def test_violation_ext_scene_at_int_only_location(self) -> None:
        loc = _loc("loc-1", supports_int_ext=("INT",))
        scene = _scene("sc-1", loc_id="loc-1", int_ext="EXT")
        D1 = date(2025, 9, 10)
        state = _state(
            scenes=(scene,),
            locations=(loc,),
            days=(_day(D1, ("sc-1",)),),
        )
        violations = validate(state, policy=_POLICY)
        c009 = [v for v in violations if v.code == "C009"]
        assert len(c009) == 1
        assert c009[0].observed == "EXT"
        assert "INT" in c009[0].required

    def test_no_violation_when_int_ext_matches(self) -> None:
        loc = _loc("loc-1", supports_int_ext=("EXT",))
        scene = _scene("sc-1", loc_id="loc-1", int_ext="EXT")
        D1 = date(2025, 9, 10)
        state = _state(
            scenes=(scene,),
            locations=(loc,),
            days=(_day(D1, ("sc-1",)),),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C009" for v in violations)

    def test_no_violation_when_supports_empty(self) -> None:
        loc = _loc("loc-1", supports_int_ext=())
        scene = _scene("sc-1", loc_id="loc-1", int_ext="EXT")
        D1 = date(2025, 9, 10)
        state = _state(
            scenes=(scene,),
            locations=(loc,),
            days=(_day(D1, ("sc-1",)),),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C009" for v in violations)


# ---------------------------------------------------------------------------
# C010 — time_of_day_match
# ---------------------------------------------------------------------------


class TestC010TimeOfDayMatch:
    def test_violation_night_scene_at_day_only_location(self) -> None:
        loc = _loc("loc-1", supports_time_of_day=("DAY",))
        scene = _scene("sc-1", loc_id="loc-1", time_of_day="NIGHT")
        D1 = date(2025, 9, 10)
        state = _state(
            scenes=(scene,),
            locations=(loc,),
            days=(_day(D1, ("sc-1",)),),
        )
        violations = validate(state, policy=_POLICY)
        c010 = [v for v in violations if v.code == "C010"]
        assert len(c010) == 1
        assert c010[0].observed == "NIGHT"
        assert "DAY" in c010[0].required

    def test_no_violation_when_time_of_day_matches(self) -> None:
        loc = _loc("loc-1", supports_time_of_day=("DAY", "NIGHT"))
        scene = _scene("sc-1", loc_id="loc-1", time_of_day="NIGHT")
        D1 = date(2025, 9, 10)
        state = _state(
            scenes=(scene,),
            locations=(loc,),
            days=(_day(D1, ("sc-1",)),),
        )
        violations = validate(state, policy=_POLICY)
        assert not any(v.code == "C010" for v in violations)


# ---------------------------------------------------------------------------
# Clean state — all rules should pass
# ---------------------------------------------------------------------------


class TestCleanState:
    def test_fully_valid_state_has_no_violations(self) -> None:
        D1 = date(2025, 9, 10)
        D2 = date(2025, 9, 11)
        sc1 = _scene("sc-1", loc_id="loc-1", int_ext="INT", time_of_day="DAY")
        sc2 = _scene("sc-2", loc_id="loc-1", int_ext="INT", time_of_day="DAY", prereq_ids=("sc-1",))
        state = _state(
            scenes=(sc1, sc2),
            locations=(_loc("loc-1", supports_int_ext=("INT",), supports_time_of_day=("DAY",)),),
            days=(
                _day(D1, ("sc-1",), call_hour=7, wrap_hour=19),
                _day(D2, ("sc-2",), call_hour=7, wrap_hour=19),
            ),
        )
        violations = validate(state, policy=_POLICY)
        assert violations == []


# ---------------------------------------------------------------------------
# Canonical SWAP test — C001 with exact 9.0h
# ---------------------------------------------------------------------------


class TestSwapProducesC001:
    """Plan B's failure, reduced to two days.

    The demo shape in miniature.  A swap moves the whole shooting day — its
    location, its call and wrap times, and its scenes — so exchanging a late
    warehouse day with an early courtyard day collapses the rest between them.

      Sep 10: call 07:00, wrap 19:00   (courtyard)
      Sep 11: call 12:00, wrap 22:00   (warehouse)

    Baseline turnaround: 19:00 to 12:00 next day = 17h, comfortably legal.
    After the swap Sep 10 wraps at 22:00 and Sep 11 calls at 07:00 = 9.0h.

    The same assertion against the real fixture lives in
    ``tests/engine/test_demo_fixture.py``; this one keeps the rule honest
    without loading the whole production.
    """

    D1 = date(2025, 9, 10)
    D2 = date(2025, 9, 11)

    def _base_state(self) -> ProductionState:
        sc1 = _scene("sc-1")
        sc2 = _scene("sc-2")
        return _state(
            scenes=(sc1, sc2),
            locations=(_loc(),),
            days=(
                _day(self.D1, ("sc-1",), call_hour=7, wrap_hour=19),
                _day(self.D2, ("sc-2",), call_hour=12, wrap_hour=22),
            ),
        )

    def test_base_state_is_clean(self) -> None:
        """17h of turnaround: the baseline must not carry the violation itself."""
        violations = validate(self._base_state(), policy=_POLICY)
        assert not any(v.code == "C001" for v in violations)

    def test_swap_days_produces_exactly_one_c001(self) -> None:
        swapped = self._base_state().apply([SwapDays(date_a=self.D1, date_b=self.D2)])
        violations = validate(swapped, policy=_POLICY)
        c001 = [v for v in violations if v.code == "C001"]
        assert len(c001) == 1
        assert c001[0].observed == "9.0h"
        assert ">= 10.0h" in c001[0].required

    def test_swap_violation_is_hard(self) -> None:
        swapped = self._base_state().apply([SwapDays(date_a=self.D1, date_b=self.D2)])
        violations = validate(swapped, policy=_POLICY)
        c001 = [v for v in violations if v.code == "C001"]
        assert c001[0].severity == "HARD"

    def test_shifting_the_call_repairs_it(self) -> None:
        """The B-to-B2 repair: a later call restores the ten-hour minimum."""
        swapped = self._base_state().apply([SwapDays(date_a=self.D1, date_b=self.D2)])
        repaired = swapped.apply(
            [
                ShiftCallTime(
                    date=self.D2,
                    new_call_time=datetime(2025, 9, 11, 8, 30, tzinfo=UTC),
                )
            ]
        )
        violations = validate(repaired, policy=_POLICY)
        assert not any(v.code == "C001" for v in violations)


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------


class TestRuleRegistry:
    def test_all_ten_rules_registered(self) -> None:
        assert len(RULES) == 10
        assert len(RULE_CODES) == 10

    def test_codes_are_in_order(self) -> None:
        assert list(RULE_CODES) == sorted(RULE_CODES)
        assert RULE_CODES[0] == "C001"
        assert RULE_CODES[-1] == "C010"

    def test_every_rule_is_callable_with_the_rule_signature(self) -> None:
        day = _day(date(2025, 9, 10), ("sc-1",))
        state = _state(scenes=(_scene("sc-1"),), locations=(_loc(),), days=(day,))
        for rule in RULES:
            assert isinstance(rule(state, _POLICY), list)


# ---------------------------------------------------------------------------
# The canonical scenario, against the real fixture
# ---------------------------------------------------------------------------


class TestSwapScenario:
    """SwapDays(Sep 10, Sep 11) on the demo fixture produces exactly one C001.

    This is the assertion the demo narration depends on. The validator observed
    9.0h, the policy required at least 10.0h, and those two strings are read
    aloud on camera — so they are pinned here against the real production, not
    a reduced-case stand-in.
    """

    SEP_10 = date(2026, 9, 10)
    SEP_11 = date(2026, 9, 11)

    def _swapped(self, night_train_state: ProductionState) -> ProductionState:
        return night_train_state.apply([SwapDays(date_a=self.SEP_10, date_b=self.SEP_11)])

    def test_baseline_fixture_is_legal(self, night_train_state: ProductionState) -> None:
        assert [v.code for v in validate(night_train_state) if v.severity == "HARD"] == []

    def test_exactly_one_c001(self, night_train_state: ProductionState) -> None:
        hard = [v for v in validate(self._swapped(night_train_state)) if v.severity == "HARD"]
        assert len(hard) == 1
        assert hard[0].code == "C001"

    def test_observed_value_is_nine_hours(self, night_train_state: ProductionState) -> None:
        violation = next(v for v in validate(self._swapped(night_train_state)) if v.code == "C001")
        assert violation.observed == "9.0h"
        assert violation.required == ">= 10.0h"
        assert violation.subject_ids == ("2026-09-10", "2026-09-11")
