"""The sample workbooks a first-time user can try before they have data.

Two of them, and the second one is the important one.

``night_train.xlsx`` is the seeded demo production exported through the same
path a user's own file takes. It proves the exporter and the parser agree.

``second_unit.xlsx`` — *Harbour Lights* — exists to prove the engine is not the
fixture. It is a different shape in every way that matters: a different
timezone, twice the locations, two units, an overnight shoot, an equipment
rental window, crew named on a scene, and **one deliberately planted constraint
violation** so the review screen's Schedule-health tab has something real to
show. A disruption runs against it end to end with no fixture-specific code
anywhere in the path.

Regenerate both with::

    python -m pri.importer.samples
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from pri.domain.models import (
    DisruptionEvent,
    Equipment,
    Location,
    Person,
    Production,
    ProductionState,
    Scene,
    Schedule,
    ShootingDay,
    TimeWindow,
)
from pri.importer.template import export_state
from pri.paths import data_path

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "SAMPLES_DIR",
    "SAMPLE_NAMES",
    "build_second_unit_state",
    "second_unit_disruption",
    "write_samples",
]

SAMPLES_DIR = data_path("samples")

SAMPLE_NAMES: dict[str, str] = {
    "night_train": "Night Train to Kochi — the demo production",
    "second_unit": "Harbour Lights — two units, an overnight, and a planted violation",
}

_ZONE = ZoneInfo("Europe/London")
_PRODUCTION_ID = "film-hl-002"

#: March 2027, comfortably before the UK clocks change on the 28th. Chosen so
#: the sample exercises a non-UTC zone without also exercising DST — the DST
#: edges have their own tests, and a sample that fails on one date a year is a
#: bad sample.
_START = date(2027, 3, 1)
_END = date(2027, 3, 20)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=_ZONE)


def _window(start: date, end_inclusive: date) -> TimeWindow:
    return TimeWindow(
        start=datetime.combine(start, time.min, tzinfo=_ZONE),
        end=datetime.combine(end_inclusive + timedelta(days=1), time.min, tzinfo=_ZONE),
    )


def _daily_permit(start_hour: int, end_hour: int) -> tuple[TimeWindow, ...]:
    """A recurring window across the whole shoot, the way the parser builds it."""
    windows: list[TimeWindow] = []
    cursor = _START
    while cursor <= _END:
        opens = _at(cursor, start_hour)
        closes = _at(cursor, end_hour)
        if closes <= opens:
            closes = _at(cursor + timedelta(days=1), end_hour)
        windows.append(TimeWindow(start=opens, end=closes))
        cursor += timedelta(days=1)
    return tuple(windows)


def build_second_unit_state() -> ProductionState:
    """Build *Harbour Lights* — the deliberately un-fixture-like production.

    Outputs:
        A version-1 ``ProductionState`` carrying exactly one pre-existing
        constraint violation: C001, six hours of turnaround between the
        overnight dock shoot and the following morning.
    """
    people = (
        Person(
            id="HL-C1",
            name="Ruth Okonkwo",
            role="CAST",
            character="Ruth",
            daily_rate=Decimal("3100"),
            unavailable_windows=(),
        ),
        Person(
            id="HL-C2",
            name="Tomas Brand",
            role="CAST",
            character="Tomas",
            daily_rate=Decimal("2750"),
            unavailable_windows=(_window(date(2027, 3, 9), date(2027, 3, 11)),),
        ),
        Person(
            id="HL-C3",
            name="Isla Vance",
            role="CAST",
            character="Isla",
            daily_rate=Decimal("1900"),
            unavailable_windows=(),
        ),
        Person(
            id="HL-C4",
            name="Owen Pryce",
            role="CAST",
            character="Owen",
            daily_rate=Decimal("1450"),
            unavailable_windows=(),
        ),
        Person(
            id="HL-C5",
            name="Nadia Roth",
            role="CAST",
            character="Nadia",
            daily_rate=Decimal("1200"),
            unavailable_windows=(),
        ),
        # Named crew on a scene — something the seeded fixture never exercises.
        Person(
            id="HL-X1",
            name="Gita Raman",
            role="CREW",
            character=None,
            daily_rate=Decimal("880"),
            unavailable_windows=(),
        ),
    )

    locations = (
        Location(
            id="HL-STUDIO",
            name="Bridgewater Stage 2",
            kind="studio",
            day_rate=Decimal("2600"),
            permit_windows=(),
            supports_int_ext=("INT",),
            supports_time_of_day=("DAY", "NIGHT"),
            address="Bridgewater Studios, Stage 2, Ordsall Lane, Salford M5 3EN",
            parking_note="Unit base in the west yard; crew parking on Ordsall Lane",
            nearest_hospital="Salford Royal Hospital, Stott Lane - 0161 789 7373",
        ),
        Location(
            id="HL-DOCK",
            name="Albert Dock, East Quay",
            kind="dock",
            day_rate=Decimal("5400"),
            permit_windows=_daily_permit(16, 4),
            supports_int_ext=("EXT",),
            supports_time_of_day=("NIGHT", "DUSK"),
            address="Albert Dock, East Quay, Liverpool L3 4AF",
            parking_note="Trucks on the quay apron; cast cars at the Salthouse lot",
            nearest_hospital="Royal Liverpool University Hospital - 0151 706 2000",
        ),
        Location(
            id="HL-PUB",
            name="The Anchor, back bar",
            kind="practical",
            day_rate=Decimal("1800"),
            permit_windows=_daily_permit(9, 22),
            supports_int_ext=("INT",),
            supports_time_of_day=("DAY", "NIGHT", "DUSK"),
            address="The Anchor, 12 Mariners Row, Liverpool L3 1DR",
            parking_note="No unit vehicles on Mariners Row; basecamp at the Strand",
            nearest_hospital="Royal Liverpool University Hospital - 0151 706 2000",
        ),
        Location(
            id="HL-ROOF",
            name="Warehouse rooftop",
            kind="exterior",
            day_rate=Decimal("3200"),
            permit_windows=_daily_permit(7, 19),
            supports_int_ext=("EXT",),
            supports_time_of_day=("DAY", "DAWN"),
            address="Warehouse 9 rooftop, Regent Road, Liverpool L3 7BN",
            parking_note="Lift access from Regent Road; no parking at street level",
            nearest_hospital="Royal Liverpool University Hospital - 0151 706 2000",
        ),
    )

    equipment = (
        Equipment(
            id="HL-CAM",
            name="Venice 2",
            kind="camera",
            daily_rate=Decimal("1150"),
            available_windows=(),
        ),
        # A rental window: the crane is only ours for the first four days.
        Equipment(
            id="HL-CRANE",
            name="Scorpio 45",
            kind="crane",
            daily_rate=Decimal("2400"),
            available_windows=(_window(date(2027, 3, 1), date(2027, 3, 4)),),
        ),
        Equipment(
            id="HL-DRONE",
            name="Alta X",
            kind="aerial",
            daily_rate=Decimal("1600"),
            available_windows=(),
        ),
    )

    scenes = (
        Scene(
            id="HL-S1",
            number="1",
            slug="INT. STAGE - BRIDGE SET - DAY",
            description="Ruth takes the helm",
            int_ext="INT",
            time_of_day="DAY",
            estimated_minutes=150,
            location_id="HL-STUDIO",
            cast_ids=("HL-C1",),
            crew_ids=(),
            equipment_ids=("HL-CAM",),
            vfx_plate=False,
            prerequisite_scene_ids=(),
        ),
        Scene(
            id="HL-S2",
            number="2",
            slug="INT. STAGE - BRIDGE SET - NIGHT",
            description="The storm hits",
            int_ext="INT",
            time_of_day="NIGHT",
            estimated_minutes=195,
            location_id="HL-STUDIO",
            cast_ids=("HL-C1", "HL-C3"),
            crew_ids=("HL-X1",),
            equipment_ids=("HL-CAM", "HL-CRANE"),
            vfx_plate=True,
            prerequisite_scene_ids=(),
        ),
        Scene(
            id="HL-S3",
            number="3A",
            slug="EXT. EAST QUAY - NIGHT",
            description="The container is opened",
            int_ext="EXT",
            time_of_day="NIGHT",
            estimated_minutes=210,
            location_id="HL-DOCK",
            cast_ids=("HL-C1", "HL-C4"),
            crew_ids=(),
            equipment_ids=("HL-CAM",),
            vfx_plate=True,
            prerequisite_scene_ids=(),
        ),
        Scene(
            id="HL-S4",
            number="3B",
            slug="EXT. EAST QUAY - NIGHT",
            description="Owen runs",
            int_ext="EXT",
            time_of_day="NIGHT",
            estimated_minutes=105,
            location_id="HL-DOCK",
            cast_ids=("HL-C4",),
            crew_ids=("HL-X1",),
            equipment_ids=("HL-CAM",),
            vfx_plate=False,
            prerequisite_scene_ids=(),
        ),
        Scene(
            id="HL-S5",
            number="4",
            slug="INT. THE ANCHOR - DAY",
            description="Tomas is warned off",
            int_ext="INT",
            time_of_day="DAY",
            estimated_minutes=120,
            location_id="HL-PUB",
            cast_ids=("HL-C2", "HL-C5"),
            crew_ids=(),
            equipment_ids=("HL-CAM",),
            vfx_plate=False,
            prerequisite_scene_ids=(),
        ),
        Scene(
            id="HL-S6",
            number="5",
            slug="INT. THE ANCHOR - NIGHT",
            description="The handover",
            int_ext="INT",
            time_of_day="NIGHT",
            estimated_minutes=140,
            location_id="HL-PUB",
            cast_ids=("HL-C2", "HL-C4"),
            crew_ids=(),
            equipment_ids=("HL-CAM",),
            vfx_plate=False,
            prerequisite_scene_ids=("HL-S5",),
        ),
        Scene(
            id="HL-S7",
            number="6",
            slug="EXT. ROOFTOP - DAWN",
            description="Ruth sees the boat leave",
            int_ext="EXT",
            time_of_day="DAWN",
            estimated_minutes=95,
            location_id="HL-ROOF",
            cast_ids=("HL-C1",),
            crew_ids=(),
            equipment_ids=("HL-CAM", "HL-DRONE"),
            vfx_plate=True,
            prerequisite_scene_ids=("HL-S3",),
        ),
        Scene(
            id="HL-S8",
            number="7",
            slug="EXT. ROOFTOP - DAY",
            description="Aerial plate over the docks",
            int_ext="EXT",
            time_of_day="DAY",
            estimated_minutes=75,
            location_id="HL-ROOF",
            cast_ids=(),
            crew_ids=("HL-X1",),
            equipment_ids=("HL-DRONE",),
            vfx_plate=True,
            prerequisite_scene_ids=(),
        ),
        Scene(
            id="HL-S9",
            number="8",
            slug="INT. STAGE - HOLD - DAY",
            description="Nadia finds the manifest",
            int_ext="INT",
            time_of_day="DAY",
            estimated_minutes=110,
            location_id="HL-STUDIO",
            cast_ids=("HL-C5",),
            crew_ids=(),
            equipment_ids=("HL-CAM",),
            vfx_plate=False,
            prerequisite_scene_ids=(),
        ),
    )

    days = (
        ShootingDay(
            date=date(2027, 3, 1),
            call_time=_at(date(2027, 3, 1), 8),
            wrap_time=_at(date(2027, 3, 1), 18),
            location_id="HL-STUDIO",
            scene_ids=("HL-S1",),
            unit="MAIN",
        ),
        # The overnight: the dock permit opens at 16:00 and runs to 04:00, and
        # this day wraps at 02:00 the following morning.
        ShootingDay(
            date=date(2027, 3, 2),
            call_time=_at(date(2027, 3, 2), 18),
            wrap_time=_at(date(2027, 3, 3), 2),
            location_id="HL-DOCK",
            scene_ids=("HL-S3", "HL-S4"),
            unit="MAIN",
        ),
        # THE PLANTED VIOLATION. Wrapping at 02:00 and calling again at 08:00 is
        # six hours of turnaround against a ten-hour minimum: C001. A real board
        # arrives with exactly this on it, and the point of the review screen is
        # to say so rather than to refuse the file.
        ShootingDay(
            date=date(2027, 3, 3),
            call_time=_at(date(2027, 3, 3), 8),
            wrap_time=_at(date(2027, 3, 3), 18),
            location_id="HL-STUDIO",
            scene_ids=("HL-S2",),
            unit="MAIN",
        ),
        ShootingDay(
            date=date(2027, 3, 4),
            call_time=_at(date(2027, 3, 4), 10),
            wrap_time=_at(date(2027, 3, 4), 20),
            location_id="HL-PUB",
            scene_ids=("HL-S5",),
            unit="MAIN",
        ),
        ShootingDay(
            date=date(2027, 3, 8),
            call_time=_at(date(2027, 3, 8), 11),
            wrap_time=_at(date(2027, 3, 8), 21),
            location_id="HL-PUB",
            scene_ids=("HL-S6",),
            unit="MAIN",
        ),
        ShootingDay(
            date=date(2027, 3, 9),
            call_time=_at(date(2027, 3, 9), 8),
            wrap_time=_at(date(2027, 3, 9), 17),
            location_id="HL-STUDIO",
            scene_ids=("HL-S9",),
            unit="MAIN",
        ),
        # Second unit, running its own days at the end so the turnaround rule
        # compares like with like.
        ShootingDay(
            date=date(2027, 3, 15),
            call_time=_at(date(2027, 3, 15), 7),
            wrap_time=_at(date(2027, 3, 15), 15),
            location_id="HL-ROOF",
            scene_ids=("HL-S7",),
            unit="SECOND",
        ),
        ShootingDay(
            date=date(2027, 3, 16),
            call_time=_at(date(2027, 3, 16), 8),
            wrap_time=_at(date(2027, 3, 16), 16),
            location_id="HL-ROOF",
            scene_ids=("HL-S8",),
            unit="SECOND",
        ),
        # Reserve days, empty, so a recovery has somewhere to defer onto.
        ShootingDay(
            date=date(2027, 3, 18),
            call_time=_at(date(2027, 3, 18), 8),
            wrap_time=_at(date(2027, 3, 18), 18),
            location_id="HL-STUDIO",
            scene_ids=(),
            unit="MAIN",
        ),
        ShootingDay(
            date=date(2027, 3, 19),
            call_time=_at(date(2027, 3, 19), 8),
            wrap_time=_at(date(2027, 3, 19), 18),
            location_id="HL-STUDIO",
            scene_ids=(),
            unit="MAIN",
        ),
    )

    return ProductionState(
        production=Production(
            id=_PRODUCTION_ID,
            title="Harbour Lights",
            currency="GBP",
            shoot_start=_START,
            shoot_end=_END,
            reserve_days=(date(2027, 3, 18), date(2027, 3, 19)),
        ),
        scenes=scenes,
        people=people,
        locations=locations,
        equipment=equipment,
        schedule=Schedule(days=days),
        version=1,
        parent_version=None,
        event_id=None,
        created_at=datetime(2027, 2, 20, tzinfo=_ZONE),
    )


def second_unit_disruption() -> DisruptionEvent:
    """A dock closure for *Harbour Lights*, to prove recovery is not fixture-bound."""
    return DisruptionEvent(
        event_id="evt-hl-dock-blocked",
        production_id=_PRODUCTION_ID,
        event_type="location.blocked",
        occurred_at=_at(date(2027, 3, 1), 21),
        source="harbour_authority",
        severity=0.75,
        payload={
            "location_id": "HL-DOCK",
            "window_start": _at(date(2027, 3, 2), 0).isoformat(),
            "window_end": _at(date(2027, 3, 3), 0).isoformat(),
            "reason": "Harbour authority closed the east quay for a vessel movement",
        },
    )


def write_samples(directory: Path | None = None) -> list[Path]:
    """Write both sample workbooks to ``data/samples/``.

    Outputs:
        The paths written, in order.
    """
    from pri.persistence.seed import build_state

    target = directory or SAMPLES_DIR
    target.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for name, state in (
        ("night_train", build_state()),
        ("second_unit", build_second_unit_state()),
    ):
        path = target / f"{name}.xlsx"
        path.write_bytes(export_state(state))
        written.append(path)
    return written


def main() -> None:
    """``python -m pri.importer.samples`` — regenerate the sample workbooks."""
    for path in write_samples():
        print(f"wrote {path} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
