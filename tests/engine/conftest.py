"""Shared fixtures for engine simulation + verification tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from pri.domain.models import (
    Equipment,
    ImpactReport,
    Location,
    Person,
    Production,
    ProductionState,
    Scene,
    Schedule,
    ShootingDay,
)

D1 = date(2025, 5, 1)
D2 = date(2025, 5, 2)
D3 = date(2025, 5, 3)
D4 = date(2025, 5, 4)


def _dt(d: date, hour: int = 7) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=UTC)


@pytest.fixture()
def seed_state() -> ProductionState:
    """4-scene, 3-day production with a prerequisite chain sc-1→sc-3→sc-4.

    D1: sc-1, sc-2 at LOC-01 (INT/DAY studio)
    D2: sc-3 at LOC-04 (INT/DAY exterior)   ← blocked in disruption event
    D3: sc-4 at LOC-04 (EXT/DAY exterior)
    D4 is NOT a shooting day — used as an extra available date in some tests.
    """
    cast1 = Person(
        id="cast-01",
        name="Alice",
        role="CAST",
        character="Hero",
        daily_rate=Decimal("5000"),
        unavailable_windows=(),
    )
    cast2 = Person(
        id="cast-02",
        name="Bob",
        role="CAST",
        character="Villain",
        daily_rate=Decimal("4000"),
        unavailable_windows=(),
    )
    crew1 = Person(
        id="crew-01",
        name="Charlie",
        role="CREW",
        character=None,
        daily_rate=Decimal("1500"),
        unavailable_windows=(),
    )
    loc01 = Location(
        id="LOC-01",
        name="Studio A",
        kind="studio",
        day_rate=Decimal("2000"),
        permit_windows=(),
        supports_int_ext=("INT",),
        supports_time_of_day=("DAY",),
    )
    loc04 = Location(
        id="LOC-04",
        name="Outdoor Park",
        kind="exterior",
        day_rate=Decimal("500"),
        permit_windows=(),
        supports_int_ext=("EXT",),
        supports_time_of_day=("DAY", "DAWN", "DUSK"),
    )
    crane = Equipment(
        id="equip-01",
        name="Crane",
        kind="camera_support",
        daily_rate=Decimal("800"),
        available_windows=(),
    )
    sc1 = Scene(
        id="sc-1",
        number=1,
        slug="sc001",
        description="Opening interior",
        int_ext="INT",
        time_of_day="DAY",
        estimated_minutes=45,
        location_id="LOC-01",
        cast_ids=("cast-01",),
        equipment_ids=("equip-01",),
        vfx_plate=False,
        prerequisite_scene_ids=(),
    )
    sc2 = Scene(
        id="sc-2",
        number=2,
        slug="sc002",
        description="Interior dialogue",
        int_ext="INT",
        time_of_day="DAY",
        estimated_minutes=30,
        location_id="LOC-01",
        cast_ids=("cast-02",),
        equipment_ids=(),
        vfx_plate=False,
        prerequisite_scene_ids=(),
    )
    # sc-3 is INT but LOC-04 only supports EXT — to keep the state constraint-valid
    # for the shift/swap/trim families, we make sc-3 EXT here.
    sc3 = Scene(
        id="sc-3",
        number=3,
        slug="sc003",
        description="Park confrontation",
        int_ext="EXT",
        time_of_day="DAY",
        estimated_minutes=60,
        location_id="LOC-04",
        cast_ids=("cast-01",),
        equipment_ids=("equip-01",),
        vfx_plate=False,
        prerequisite_scene_ids=("sc-1",),
    )
    sc4 = Scene(
        id="sc-4",
        number=4,
        slug="sc004",
        description="Outdoor chase",
        int_ext="EXT",
        time_of_day="DAY",
        estimated_minutes=90,
        location_id="LOC-04",
        cast_ids=("cast-01", "cast-02"),
        equipment_ids=(),
        vfx_plate=True,
        prerequisite_scene_ids=("sc-3",),
    )
    day1 = ShootingDay(
        date=D1,
        call_time=_dt(D1, 7),
        wrap_time=_dt(D1, 19),
        location_id="LOC-01",
        scene_ids=("sc-1", "sc-2"),
    )
    day2 = ShootingDay(
        date=D2,
        call_time=_dt(D2, 7),
        wrap_time=_dt(D2, 19),
        location_id="LOC-04",
        scene_ids=("sc-3",),
    )
    day3 = ShootingDay(
        date=D3,
        call_time=_dt(D3, 7),
        wrap_time=_dt(D3, 19),
        location_id="LOC-04",
        scene_ids=("sc-4",),
    )
    day4 = ShootingDay(
        date=D4,
        call_time=_dt(D4, 7),
        wrap_time=_dt(D4, 19),
        location_id="LOC-04",
        scene_ids=(),
    )
    return ProductionState(
        production=Production(
            id="prod-seed",
            title="Seed Film",
            currency="USD",
            shoot_start=D1,
            shoot_end=D4,
            reserve_days=(),
        ),
        scenes=(sc1, sc2, sc3, sc4),
        people=(cast1, cast2, crew1),
        locations=(loc01, loc04),
        equipment=(crane,),
        schedule=Schedule(days=(day1, day2, day3, day4)),
        version=1,
        parent_version=None,
        event_id=None,
        created_at=datetime(2025, 4, 1, tzinfo=UTC),
    )


@pytest.fixture()
def loc04_blocked_report(seed_state: ProductionState) -> ImpactReport:
    """ImpactReport for LOC-04 blocked on D2 (affects sc-3, downstream sc-4)."""
    return ImpactReport(
        event_id="evt-001",
        directly_affected_scene_ids=("sc-3",),
        downstream_scene_ids=("sc-4",),
        affected_cast_ids=("cast-01",),
        affected_crew_ids=(),
        affected_location_ids=("LOC-04",),
        affected_equipment_ids=("equip-01",),
        affected_days=(D2,),
        downstream_dependency_count=1,
        blast_radius=0.5,
    )
