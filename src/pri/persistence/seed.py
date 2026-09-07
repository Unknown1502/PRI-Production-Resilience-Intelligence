"""Demo fixture loader and database seeder.

Loads ``data/fixtures/night_train.json`` — the *Night Train to Kochi* production —
and materialises it as version 1 of a :class:`~pri.domain.models.ProductionState`.

Every value in the fixture is load-bearing for the demo scenario documented in
``ibm/bob/prompts/APPENDIX_A_demo_fixture.md``.  The loader therefore performs no
rounding, no defaulting and no normalisation of times or money: what is in the
JSON is what reaches the domain model.

Two layers live here, deliberately separated:

``build_state`` / ``build_disruption_event``
    Pure functions.  No IO beyond reading the fixture file, no database.  These
    are what the engine test-suite uses.

``seed``
    Persists the built state as version 1 through :class:`PriRepository`.
    Wired to ``make seed`` via ``python -m pri.persistence.seed``.

Fixture conventions expanded here:
    ``permit_daily``    A recurring local-clock window, expanded to one concrete
                        :class:`TimeWindow` per calendar day of the shoot.  An
                        ``end`` earlier than ``start`` means the window crosses
                        midnight into the following day.
    ``available_range`` An inclusive date range, expanded to the half-open window
                        ``[from 00:00, to+1day 00:00)``.
    ``unavailable_ranges`` Same inclusive-date expansion as ``available_range``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

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
from pri.paths import data_path

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "DEFAULT_FIXTURE_PATH",
    "build_disruption_event",
    "build_state",
    "load_fixture",
    "seed",
]

# repo-root/data/fixtures/night_train.json
DEFAULT_FIXTURE_PATH = data_path("fixtures", "night_train.json")


class FixtureError(ValueError):
    """Raised when the fixture file is missing a required field or is malformed."""


# ---------------------------------------------------------------------------
# Fixture reading
# ---------------------------------------------------------------------------


def load_fixture(path: Path | None = None) -> dict[str, Any]:
    """Read and parse the fixture JSON.

    Inputs:
        path: Override the fixture location; defaults to
              ``data/fixtures/night_train.json``.

    Outputs:
        The parsed fixture as a plain dict.

    Failure modes:
        Raises ``FileNotFoundError`` if the fixture is absent.
        Raises ``json.JSONDecodeError`` if it is not valid JSON.
    """
    resolved = path if path is not None else DEFAULT_FIXTURE_PATH
    with resolved.open(encoding="utf-8") as fh:
        return cast("dict[str, Any]", json.load(fh))


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _tz(offset: str) -> timezone:
    """Build a fixed-offset timezone from an ISO offset string like ``"+05:30"``.

    Inputs:
        offset: ``"+HH:MM"`` or ``"-HH:MM"``.

    Outputs:
        A :class:`datetime.timezone` with that fixed UTC offset.

    Failure modes:
        Raises :class:`FixtureError` if the string is not a valid ISO offset.
    """
    try:
        sign = 1 if offset[0] == "+" else -1
        hours, minutes = offset[1:].split(":")
        return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
    except (IndexError, ValueError) as exc:  # pragma: no cover - malformed fixture
        raise FixtureError(f"Invalid timezone_offset {offset!r}") from exc


def _at(day: date, clock: str, tz: timezone) -> datetime:
    """Combine a calendar date with an ``"HH:MM"`` local clock time.

    Inputs:
        day:   The calendar date.
        clock: Local wall-clock time as ``"HH:MM"``.
        tz:    The production's fixed-offset timezone.

    Outputs:
        A timezone-aware ``datetime``.

    Failure modes:
        Raises :class:`FixtureError` if ``clock`` is not ``"HH:MM"``.
    """
    try:
        hour, minute = (int(part) for part in clock.split(":"))
    except ValueError as exc:
        raise FixtureError(f"Invalid clock time {clock!r}; expected 'HH:MM'") from exc
    return datetime.combine(day, time(hour, minute), tzinfo=tz)


def _daterange(start: date, end: date) -> list[date]:
    """Return every date from ``start`` to ``end`` inclusive."""
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _expand_daily_window(
    spec: dict[str, str],
    days: list[date],
    tz: timezone,
) -> tuple[TimeWindow, ...]:
    """Expand a recurring local-clock window into one concrete window per day.

    A window whose ``end`` clock time is not after its ``start`` clock time is
    treated as crossing midnight and ends on the following calendar day —
    LOC-03's ``12:00 - 02:00`` is the fixture's example.

    Inputs:
        spec: ``{"start": "HH:MM", "end": "HH:MM"}``.
        days: Calendar days to expand across.
        tz:   The production timezone.

    Outputs:
        One :class:`TimeWindow` per day, in chronological order.

    Failure modes:
        Raises :class:`FixtureError` on a malformed clock string.
    """
    windows: list[TimeWindow] = []
    for day in days:
        start = _at(day, spec["start"], tz)
        end = _at(day, spec["end"], tz)
        if end <= start:
            end += timedelta(days=1)
        windows.append(TimeWindow(start=start, end=end))
    return tuple(windows)


def _expand_date_range(spec: dict[str, str], tz: timezone) -> TimeWindow:
    """Expand an inclusive ``{"from", "to"}`` date range to a half-open window.

    ``{"from": "2026-09-09", "to": "2026-09-11"}`` becomes
    ``[2026-09-09 00:00, 2026-09-12 00:00)`` so the final day is fully covered.

    Inputs:
        spec: ``{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}``.
        tz:   The production timezone.

    Outputs:
        A single :class:`TimeWindow`.

    Failure modes:
        Raises :class:`FixtureError` if either date cannot be parsed.
    """
    try:
        first = date.fromisoformat(spec["from"])
        last = date.fromisoformat(spec["to"])
    except (KeyError, ValueError) as exc:
        raise FixtureError(f"Invalid date range {spec!r}") from exc
    return TimeWindow(
        start=datetime.combine(first, time.min, tzinfo=tz),
        end=datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz),
    )


# ---------------------------------------------------------------------------
# State construction
# ---------------------------------------------------------------------------


def build_state(
    fixture: dict[str, Any] | None = None,
    *,
    created_at: datetime | None = None,
) -> ProductionState:
    """Materialise the fixture as an unversioned-parent, version-1 state.

    The returned state is *not* validated here — asserting that it carries zero
    HARD constraint violations is the job of the test-suite, so that a fixture
    regression fails loudly in CI rather than silently at seed time.

    Inputs:
        fixture:    A parsed fixture dict; loaded from disk when omitted.
        created_at: Override the state's creation timestamp.  Supplying a fixed
                    value makes the resulting snapshot digest reproducible,
                    which the demo-reset path relies on.

    Outputs:
        A :class:`ProductionState` with ``version=1`` and ``parent_version=None``.

    Failure modes:
        Raises :class:`FixtureError` on a malformed fixture.
        Raises ``pydantic.ValidationError`` if a domain invariant is broken
        (for example a wrap time that does not follow its call time).
    """
    data = fixture if fixture is not None else load_fixture()
    tz = _tz(data.get("timezone_offset", "+00:00"))

    prod_raw = data["production"]
    shoot_start = date.fromisoformat(prod_raw["shoot_start"])
    shoot_end = date.fromisoformat(prod_raw["shoot_end"])
    all_days = _daterange(shoot_start, shoot_end)

    production = Production(
        id=prod_raw["id"],
        title=prod_raw["title"],
        currency=prod_raw["currency"],
        shoot_start=shoot_start,
        shoot_end=shoot_end,
        reserve_days=tuple(date.fromisoformat(d) for d in prod_raw["reserve_days"]),
    )

    locations = tuple(
        Location(
            id=raw["id"],
            name=raw["name"],
            kind=raw["kind"],
            day_rate=Decimal(raw["day_rate"]),
            permit_windows=(
                _expand_daily_window(raw["permit_daily"], all_days, tz)
                if raw.get("permit_daily")
                else ()
            ),
            supports_int_ext=tuple(raw["supports_int_ext"]),
            supports_time_of_day=tuple(raw["supports_time_of_day"]),
        )
        for raw in data["locations"]
    )

    people = tuple(
        Person(
            id=raw["id"],
            name=raw["name"],
            role=raw["role"],
            character=raw["character"],
            daily_rate=Decimal(raw["daily_rate"]),
            unavailable_windows=tuple(
                _expand_date_range(rng, tz) for rng in raw.get("unavailable_ranges", [])
            ),
        )
        for raw in data["people"]
    )

    equipment = tuple(
        Equipment(
            id=raw["id"],
            name=raw["name"],
            kind=raw["kind"],
            daily_rate=Decimal(raw["daily_rate"]),
            available_windows=(
                (_expand_date_range(raw["available_range"], tz),)
                if raw.get("available_range")
                else ()
            ),
        )
        for raw in data["equipment"]
    )

    scenes = tuple(
        Scene(
            id=raw["id"],
            number=raw["number"],
            slug=raw["slug"],
            description=raw["description"],
            int_ext=raw["int_ext"],
            time_of_day=raw["time_of_day"],
            estimated_minutes=raw["estimated_minutes"],
            location_id=raw["location_id"],
            cast_ids=tuple(raw["cast_ids"]),
            equipment_ids=tuple(raw["equipment_ids"]),
            vfx_plate=raw["vfx_plate"],
            prerequisite_scene_ids=tuple(raw["prerequisite_scene_ids"]),
        )
        for raw in data["scenes"]
    )

    days: list[ShootingDay] = []
    for raw in data["schedule"]:
        day_date = date.fromisoformat(raw["date"])
        call = _at(day_date, raw["call"], tz)
        wrap = _at(day_date, raw["wrap"], tz)
        if wrap <= call:  # a day that wraps after midnight
            wrap += timedelta(days=1)
        days.append(
            ShootingDay(
                date=day_date,
                call_time=call,
                wrap_time=wrap,
                location_id=raw["location_id"],
                scene_ids=tuple(raw["scene_ids"]),
                unit=raw.get("unit", "MAIN"),
                # Same inference the importer applies to a board with no
                # day_kind column: work means SHOOT, no work means RESERVE.
                # Without it the fixture's reserve days would read as empty
                # shoot days and the deferral family would find nowhere legal
                # to move a scene to.
                day_kind=raw.get("day_kind", "SHOOT" if raw["scene_ids"] else "RESERVE"),
            )
        )

    return ProductionState(
        production=production,
        scenes=scenes,
        people=people,
        locations=locations,
        equipment=equipment,
        schedule=Schedule(days=tuple(sorted(days, key=lambda d: d.date))),
        version=1,
        parent_version=None,
        event_id=None,
        created_at=created_at if created_at is not None else datetime.now(UTC),
    )


def build_disruption_event(fixture: dict[str, Any] | None = None) -> DisruptionEvent:
    """Build the canonical LOC-04-blocked event from the fixture.

    This is the event the demo publishes to Confluent and the one every
    end-to-end test replays.

    Inputs:
        fixture: A parsed fixture dict; loaded from disk when omitted.

    Outputs:
        The :class:`DisruptionEvent` described under the fixture's
        ``disruption`` key.

    Failure modes:
        Raises :class:`FixtureError` if the ``disruption`` block is absent.
    """
    data = fixture if fixture is not None else load_fixture()
    raw = data.get("disruption")
    if raw is None:
        raise FixtureError("Fixture has no 'disruption' block")
    tz = _tz(data.get("timezone_offset", "+00:00"))

    occurred_at = datetime.fromisoformat(raw["occurred_at"])
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=tz)

    payload = dict(raw["payload"])
    for key in ("window_start", "window_end"):
        if key in payload:
            parsed = datetime.fromisoformat(str(payload[key]))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            payload[key] = parsed.isoformat()

    return DisruptionEvent(
        event_id=raw["event_id"],
        production_id=raw["production_id"],
        event_type=raw["event_type"],
        occurred_at=occurred_at,
        source=raw["source"],
        severity=float(raw["severity"]),
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Database seeding
# ---------------------------------------------------------------------------


#: Productions a judge can reset to without uploading anything. The fixture is
#: the demo; the sample is a second, unrelated production that arrived through
#: the importer, which is the one that proves PRI is not hard-wired to one film.
SAMPLES: dict[str, str] = {"harbour_lights": "film-hl-002"}


async def seed(
    production_id: str = "film-001",
    reset: bool = True,
    *,
    fixture_path: Path | None = None,
    sample: str | None = None,
) -> ProductionState:
    """Insert the fixture as version 1 of ``production_id``.

    Inputs:
        production_id: Must match the fixture's production id; supplied
                       explicitly so a mismatch fails loudly rather than
                       seeding the wrong production.
        reset:         When ``True`` (the default) every existing row for the
                       production — state versions, events, sessions, plans,
                       approvals, audit entries and artifacts — is deleted
                       first.  When ``False`` the insert fails if version 1
                       already exists.
        fixture_path:  Override the fixture location.

    Outputs:
        The persisted version-1 :class:`ProductionState`.

    Failure modes:
        Raises :class:`FixtureError` if ``production_id`` does not match the
        fixture, or if ``sample`` names something that does not exist.
        Raises ``sqlalchemy.exc.SQLAlchemyError`` if the database is
        unreachable or the schema has not been migrated.
        Raises :class:`~pri.persistence.errors.StaleStateError` when
        ``reset=False`` and version 1 is already present.
    """
    from sqlalchemy import text

    from pri.config import get_settings
    from pri.persistence.database import SessionFactory, build_engine
    from pri.persistence.repository import PriRepository

    if sample is not None:
        if sample not in SAMPLES:
            raise FixtureError(f"Unknown sample {sample!r}. Available: {', '.join(SAMPLES)}")
        # Imported here rather than at module scope: the samples module builds
        # workbooks and pulls in openpyxl, which the seed path does not need.
        from pri.importer.samples import build_second_unit_state

        state = build_second_unit_state()
        production_id = state.production.id
    else:
        fixture = load_fixture(fixture_path)
        state = build_state(fixture)
        if state.production.id != production_id:
            raise FixtureError(
                f"production_id {production_id!r} does not match fixture {state.production.id!r}"
            )

    settings = get_settings()
    engine = build_engine(settings.database_dsn)
    factory = SessionFactory(engine)
    try:
        if reset:
            # Ordered to respect foreign keys: children first, and state_versions
            # before events because it carries a deferred FK onto event_id.
            by_session = (
                "session_id IN (SELECT id FROM recovery_sessions WHERE production_id = :pid)"
            )
            statements = (
                f"DELETE FROM approvals WHERE {by_session}",
                f"DELETE FROM candidate_plans WHERE {by_session}",
                "DELETE FROM recovery_sessions WHERE production_id = :pid",
                "DELETE FROM artifacts WHERE production_id = :pid",
                "DELETE FROM audit_log WHERE production_id = :pid",
                "DELETE FROM state_versions WHERE production_id = :pid",
                "DELETE FROM events WHERE production_id = :pid",
                # Staged imports too. A reset that leaves a half-reviewed
                # upload behind is not a reset, and the staging row holds a
                # parsed payload for a production that no longer exists.
                "DELETE FROM import_staging WHERE production_id = :pid "
                "OR status = 'PENDING_REVIEW'",
            )
            async with factory() as session:
                for statement in statements:
                    await session.execute(text(statement), {"pid": production_id})
        repo = PriRepository(factory)
        await repo.commit_state(state, event_id=None)
    finally:
        await engine.dispose()
    return state


def main() -> None:
    """``make seed`` entrypoint: seed the demo production and print a summary."""
    state = asyncio.run(seed())
    print(
        f"Seeded {state.production.title} ({state.production.id}) "
        f"as version {state.version}: "
        f"{len(state.scenes)} scenes, {len(state.schedule.days)} shooting days."
    )


if __name__ == "__main__":
    main()
