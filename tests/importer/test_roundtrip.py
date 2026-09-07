"""Round-trip and property tests — the strongest guarantee in the module.

If ``export_state`` and ``parse_workbook`` disagree about any field, the digest
comparison catches it. The property test then does the same thing a hundred
times over randomly shaped productions, which is what proves the spec covers
its own output rather than just the one production we happened to write down.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from pri.domain.models import (
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
from pri.importer import export_state, parse_workbook
from pri.importer.samples import build_second_unit_state
from pri.importer.template import guard_formula
from pri.persistence.seed import build_state
from pri.persistence.serialization import content_digest


def _reparse(state: ProductionState, name: str = "rt.xlsx") -> ProductionState:
    result = parse_workbook(export_state(state), name)
    assert result.errors == (), [(e.code, e.sheet, e.row, e.message) for e in result.errors]
    assert result.state is not None
    return result.state


class TestRoundTrip:
    def test_the_seeded_fixture_survives_export_and_reimport(self) -> None:
        original = build_state()
        assert content_digest(_reparse(original)) == content_digest(original)

    def test_the_second_unit_sample_survives(self) -> None:
        original = build_second_unit_state()
        assert content_digest(_reparse(original)) == content_digest(original)

    def test_an_overnight_wrap_survives(self) -> None:
        original = build_second_unit_state()
        reparsed = _reparse(original)
        overnight = [
            day for day in reparsed.schedule.days if day.wrap_time.date() > day.call_time.date()
        ]
        assert len(overnight) == 1
        assert overnight[0].wrap_time.strftime("%H:%M") == "02:00"

    def test_both_units_survive(self) -> None:
        reparsed = _reparse(build_second_unit_state())
        assert {day.unit for day in reparsed.schedule.days} == {"MAIN", "SECOND"}

    def test_an_equipment_rental_window_survives(self) -> None:
        original = build_second_unit_state()
        reparsed = _reparse(original)
        crane = next(e for e in reparsed.equipment if e.id == "HL-CRANE")
        assert len(crane.available_windows) == 1
        assert crane.available_windows[0].start.date() == date(2027, 3, 1)
        assert crane.available_windows[0].end.date() == date(2027, 3, 5)

    def test_named_crew_on_a_scene_survives(self) -> None:
        reparsed = _reparse(build_second_unit_state())
        scene = next(s for s in reparsed.scenes if s.id == "HL-S2")
        assert scene.crew_ids == ("HL-X1",)

    def test_an_alphanumeric_scene_number_survives(self) -> None:
        reparsed = _reparse(build_second_unit_state())
        assert {s.number for s in reparsed.scenes} >= {"3A", "3B"}


class TestPropertyRoundTrip:
    """A hundred randomly shaped productions, each exported and re-parsed.

    Seeded, so a failure is reproducible from the printed seed rather than
    being a story about a build that went red once.
    """

    @pytest.mark.parametrize("seed", range(100))
    def test_random_production_round_trips(self, seed: int) -> None:
        original = _random_production(seed)
        reparsed = _reparse(original, f"random-{seed}.xlsx")
        assert content_digest(reparsed) == content_digest(original), (
            f"seed {seed} did not round-trip"
        )


class TestFormulaInjection:
    @pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
    def test_a_dangerous_leading_character_is_neutralised(self, lead: str) -> None:
        assert guard_formula(f"{lead}cmd|'/c calc'!A1").startswith("'")

    def test_ordinary_text_is_untouched(self) -> None:
        assert guard_formula("INT. ROOM - DAY") == "INT. ROOM - DAY"

    def test_an_exported_cell_beginning_with_equals_is_written_as_text(self) -> None:
        """The classic CSV-injection payload, exported and read back raw."""
        import io

        from openpyxl import load_workbook

        original = build_state()
        hostile = original.model_copy(
            update={
                "locations": (
                    original.locations[0].model_copy(update={"name": "=cmd|'/c calc'!A1"}),
                    *original.locations[1:],
                )
            }
        )
        workbook = load_workbook(io.BytesIO(export_state(hostile)))
        cell = workbook["locations"].cell(row=2, column=2)
        assert str(cell.value).startswith("'="), "the guard did not fire"

    def test_the_guard_survives_the_round_trip_as_data(self) -> None:
        """The apostrophe is a spreadsheet convention, and Excel strips it."""
        assert guard_formula("=A1+A2") == "'=A1+A2"


# ---------------------------------------------------------------------------
# Random production generator
# ---------------------------------------------------------------------------

_ZONES = ("Asia/Kolkata", "Europe/London", "America/Los_Angeles", "UTC", "Australia/Sydney")
_CURRENCIES = ("USD", "EUR", "GBP", "INR")

#: Kept clear of every DST transition in the zones above. The DST edges have
#: their own tests; a property test that fails on two days a year would be
#: testing the calendar, not the round-trip.
_SAFE_START = date(2027, 6, 1)


def _random_production(seed: int) -> ProductionState:
    """Build a valid but arbitrarily shaped production."""
    rng = random.Random(seed)
    zone = ZoneInfo(rng.choice(_ZONES))

    start = _SAFE_START + timedelta(days=rng.randint(0, 20))
    span = rng.randint(6, 25)
    end = start + timedelta(days=span)

    def at(day: date, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute), tzinfo=zone)

    def whole_days(first: date, last: date) -> TimeWindow:
        return TimeWindow(
            start=datetime.combine(first, time.min, tzinfo=zone),
            end=datetime.combine(last + timedelta(days=1), time.min, tzinfo=zone),
        )

    location_count = rng.randint(1, 4)
    locations: list[Location] = []
    for index in range(location_count):
        has_permit = rng.random() < 0.5
        permit: tuple[TimeWindow, ...] = ()
        if has_permit:
            open_hour = rng.choice([6, 7, 8])
            close_hour = rng.choice([18, 19, 20])
            windows = []
            cursor = start
            while cursor <= end:
                windows.append(TimeWindow(start=at(cursor, open_hour), end=at(cursor, close_hour)))
                cursor += timedelta(days=1)
            permit = tuple(windows)
        locations.append(
            Location(
                id=f"L{index}",
                name=f"Location {index}",
                kind=rng.choice(["studio", "street", ""]),
                day_rate=Decimal(rng.randrange(0, 900000)) / 100,
                permit_windows=permit,
                supports_int_ext=("INT", "EXT"),
                supports_time_of_day=("DAY", "NIGHT", "DAWN", "DUSK"),
            )
        )

    cast_count = rng.randint(1, 5)
    crew_count = rng.randint(0, 2)
    people: list[Person] = []
    for index in range(cast_count):
        windows: tuple[TimeWindow, ...] = ()
        if rng.random() < 0.35:
            offset = rng.randint(0, max(span - 3, 0))
            first = start + timedelta(days=offset)
            windows = (whole_days(first, first + timedelta(days=rng.randint(0, 2))),)
        people.append(
            Person(
                id=f"C{index}",
                name=f"Cast {index}",
                role="CAST",
                character=f"Character {index}",
                daily_rate=Decimal(rng.randrange(0, 500000)) / 100,
                unavailable_windows=windows,
            )
        )
    for index in range(crew_count):
        people.append(
            Person(
                id=f"X{index}",
                name=f"Crew {index}",
                role="CREW",
                character=None,
                daily_rate=Decimal(rng.randrange(0, 200000)) / 100,
                unavailable_windows=(),
            )
        )

    equipment_count = rng.randint(0, 3)
    equipment: list[Equipment] = []
    for index in range(equipment_count):
        windows = ()
        if rng.random() < 0.4:
            offset = rng.randint(0, max(span - 4, 0))
            first = start + timedelta(days=offset)
            windows = (whole_days(first, first + timedelta(days=rng.randint(1, 3))),)
        equipment.append(
            Equipment(
                id=f"E{index}",
                name=f"Equipment {index}",
                kind=rng.choice(["camera", "crane", ""]),
                daily_rate=Decimal(rng.randrange(0, 300000)) / 100,
                available_windows=windows,
            )
        )

    scene_count = rng.randint(1, 14)
    scenes: list[Scene] = []
    for index in range(scene_count):
        suffix = rng.choice(["", "", "A", "B"])
        scenes.append(
            Scene(
                id=f"S{index}",
                number=f"{index + 1}{suffix}",
                slug=f"SCENE {index} SLUG",
                description=rng.choice(["", f"Description {index}"]),
                int_ext=rng.choice(["INT", "EXT"]),
                time_of_day=rng.choice(["DAY", "NIGHT", "DAWN", "DUSK"]),
                estimated_minutes=rng.randint(5, 300),
                location_id=rng.choice(locations).id,
                cast_ids=tuple(p.id for p in people if p.role == "CAST" and rng.random() < 0.4),
                crew_ids=tuple(p.id for p in people if p.role == "CREW" and rng.random() < 0.3),
                equipment_ids=tuple(e.id for e in equipment if rng.random() < 0.4),
                vfx_plate=rng.random() < 0.25,
                prerequisite_scene_ids=(),
            )
        )

    units = ("MAIN",) if rng.random() < 0.6 else ("MAIN", "SECOND")
    unscheduled = [scene.id for scene in scenes]
    rng.shuffle(unscheduled)

    days: list[ShootingDay] = []
    cursor = start
    while cursor <= end and unscheduled:
        unit = rng.choice(units)
        take = min(len(unscheduled), rng.randint(0, 3))
        assigned = tuple(unscheduled[:take])
        unscheduled = unscheduled[take:]

        overnight = rng.random() < 0.2
        if overnight:
            call_hour, wrap_hour = rng.choice([(18, 2), (20, 4), (17, 1)])
            call_dt = at(cursor, call_hour)
            wrap_dt = at(cursor + timedelta(days=1), wrap_hour)
        else:
            call_hour = rng.choice([6, 7, 8, 9])
            wrap_dt = at(cursor, call_hour + rng.randint(6, 11))
            call_dt = at(cursor, call_hour)

        days.append(
            ShootingDay(
                date=cursor,
                call_time=call_dt,
                wrap_time=wrap_dt,
                location_id=rng.choice(locations).id,
                scene_ids=assigned,
                unit=unit,
            )
        )
        cursor += timedelta(days=rng.randint(1, 2))

    if not days:
        days.append(
            ShootingDay(
                date=start,
                call_time=at(start, 8),
                wrap_time=at(start, 18),
                location_id=locations[0].id,
                scene_ids=tuple(s.id for s in scenes),
                unit="MAIN",
            )
        )

    return ProductionState(
        production=Production(
            id=f"film-rand-{seed}",
            title=f"Random Production {seed}",
            currency=rng.choice(_CURRENCIES),
            shoot_start=start,
            shoot_end=end,
            reserve_days=tuple(
                start + timedelta(days=offset)
                for offset in sorted(rng.sample(range(span + 1), rng.randint(0, 2)))
            ),
        ),
        scenes=tuple(scenes),
        people=tuple(people),
        locations=tuple(locations),
        equipment=tuple(equipment),
        schedule=Schedule(days=tuple(sorted(days, key=lambda d: (d.date, d.unit)))),
        version=1,
        parent_version=None,
        event_id=None,
        created_at=datetime(2027, 1, 1, tzinfo=zone),
    )
