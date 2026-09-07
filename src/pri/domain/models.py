"""PRI domain model.

All models are immutable (``frozen=True``).  No database, IO, or LLM code lives
here.  This module is a pure value-object layer; it may be imported anywhere.

Versioned state:
    ``ProductionState`` is the canonical snapshot of everything known about a
    production at a point in time.  The only way to evolve state is through
    ``ProductionState.apply(moves)``, which returns a **new** instance with
    ``version = parent.version + 1`` and ``parent_version = parent.version``.
    The input state is never mutated.

Move discriminated union:
    ``Move`` is the union of all atomic schedule-edit operations.  Each variant
    carries only the data needed to describe **what** should change; **how** to
    recompute constraints is the engine's responsibility.

Imports required by callers:
    All public names are listed in ``__all__``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

__all__ = [
    "MONEY_SCALE",
    "CandidatePlan",
    "ConstraintViolation",
    "DayKind",
    "DisruptionEvent",
    "Equipment",
    "EvaluatedPlan",
    "ImpactReport",
    "Location",
    "Move",
    "MoveSceneToDay",
    "Person",
    "PlanScore",
    "Production",
    "ProductionState",
    "RecoveryResult",
    "RelocateScene",
    "RoundTrace",
    "Scene",
    "Schedule",
    "ShiftCallTime",
    "ShootingDay",
    "SwapDays",
    "TimeWindow",
]


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

#: Every monetary amount is held to two decimal places. Fixing the scale in one
#: place means ``Decimal("2400")`` and ``Decimal("2400.00")`` cannot both exist
#: in a state — they are the same amount, and two spellings of it would produce
#: two different snapshot digests for the same production.
MONEY_SCALE = Decimal("0.01")

#: What a row on the board is for.
#:
#: ``scene_ids`` being empty is not enough to tell these apart, and the
#: difference matters: a recovery plan may fill a RESERVE day and must never
#: touch a TRAVEL day, a COMPANY_MOVE or a HOLD.
DayKind = Literal["SHOOT", "TRAVEL", "COMPANY_MOVE", "HOLD", "RESERVE"]


def quantise_money(value: object) -> object:
    """Normalise an amount to two decimal places, half-up.

    Used as a ``mode="before"`` validator on every rate field. Non-numeric
    input is passed through untouched so Pydantic produces its own error rather
    than a confusing one from here.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal | int | str):
        try:
            return Decimal(value).quantize(MONEY_SCALE, rounding=ROUND_HALF_UP)
        except (ArithmeticError, ValueError):
            return value
    if isinstance(value, float):
        return Decimal(str(value)).quantize(MONEY_SCALE, rounding=ROUND_HALF_UP)
    return value


# ---------------------------------------------------------------------------
# Primitive value objects
# ---------------------------------------------------------------------------


class TimeWindow(BaseModel, frozen=True):
    """A half-open time interval ``[start, end)``.

    Inputs:
        start: Inclusive lower bound (UTC datetime recommended).
        end:   Exclusive upper bound; must be strictly after ``start``.

    Failure modes:
        Raises ``ValueError`` if ``end <= start``.
    """

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _end_after_start(self) -> TimeWindow:
        if self.end <= self.start:
            raise ValueError(f"TimeWindow end ({self.end}) must be after start ({self.start})")
        return self


# ---------------------------------------------------------------------------
# Core entities
# ---------------------------------------------------------------------------


class Production(BaseModel, frozen=True):
    """Top-level production record.

    Inputs:
        id:           Unique production identifier.
        title:        Human-readable title.
        currency:     ISO-4217 currency code (e.g. ``"USD"``).
        shoot_start:  First scheduled shoot date (inclusive).
        shoot_end:    Last scheduled shoot date (inclusive).
        reserve_days: Dates held in reserve (no shooting scheduled).

    Failure modes:
        Raises ``ValueError`` if ``shoot_end < shoot_start``.
    """

    id: str
    title: str
    currency: str
    shoot_start: date
    shoot_end: date
    reserve_days: tuple[date, ...]

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Production:
        if self.shoot_end < self.shoot_start:
            raise ValueError(
                f"shoot_end ({self.shoot_end}) must not precede shoot_start ({self.shoot_start})"
            )
        return self


class Scene(BaseModel, frozen=True):
    """A single scene in the production.

    Inputs:
        id:                    Unique scene identifier.
        number:                Script scene number **as written**, e.g. ``"12A"``.
                               Text, not a number: 12 and 12A are different
                               scenes and a renumbered script produces 12A, 12B,
                               12C.  An integer is accepted and stringified so
                               a workbook that stores it numerically still
                               loads.
        slug:                  URL-safe short label (e.g. ``"sc042"``).
        description:           Brief human-readable description.
        int_ext:               Interior or exterior.
        time_of_day:           Required lighting period.
        estimated_minutes:     Estimated shoot duration in minutes (> 0).
        location_id:           References a ``Location.id``.
        cast_ids:              Ordered list of ``Person.id`` values (role CAST).
        crew_ids:              Specific crew the scene needs (role CREW).  Most
                               scenes name none — the unit is assumed — so this
                               defaults to empty.
        equipment_ids:         Ordered list of ``Equipment.id`` values required.
        vfx_plate:             Whether a VFX plate must be captured.
        prerequisite_scene_ids: Scenes that must be shot before this one.

    Failure modes:
        Raises ``ValueError`` if ``estimated_minutes <= 0``.
    """

    id: str
    number: str
    slug: str
    description: str
    int_ext: Literal["INT", "EXT"]
    time_of_day: Literal["DAY", "NIGHT", "DAWN", "DUSK"]
    estimated_minutes: int
    location_id: str
    cast_ids: tuple[str, ...]
    equipment_ids: tuple[str, ...]
    vfx_plate: bool
    prerequisite_scene_ids: tuple[str, ...]
    crew_ids: tuple[str, ...] = ()

    @field_validator("number", mode="before")
    @classmethod
    def _number_as_written(cls, value: object) -> object:
        """Accept a numeric scene number without turning 12 into ``"12.0"``.

        Scene numbers are text — 12A is a real scene number and a renumbered
        script is full of them — but a spreadsheet column holding 12 arrives as
        an int or a float, and the naive stringification of the float is wrong.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return value

    @model_validator(mode="after")
    def _positive_duration(self) -> Scene:
        if self.estimated_minutes <= 0:
            raise ValueError(f"estimated_minutes must be > 0, got {self.estimated_minutes}")
        return self


class Person(BaseModel, frozen=True):
    """A cast or crew member.

    Inputs:
        id:                  Unique person identifier.
        name:                Full name.
        role:                ``"CAST"`` or ``"CREW"``.
        character:           Character name; ``None`` for crew.
        daily_rate:          Day rate in the production's currency.
        unavailable_windows: Time ranges when the person cannot work.

    Failure modes:
        Raises ``ValueError`` if ``daily_rate < 0``.
    """

    id: str
    name: str
    role: Literal["CAST", "CREW"]
    character: str | None
    daily_rate: Decimal
    unavailable_windows: tuple[TimeWindow, ...]

    _quantise_rate = field_validator("daily_rate", mode="before")(quantise_money)

    @model_validator(mode="after")
    def _non_negative_rate(self) -> Person:
        if self.daily_rate < Decimal(0):
            raise ValueError(f"daily_rate must be >= 0, got {self.daily_rate}")
        return self


class Location(BaseModel, frozen=True):
    """A filming location.

    Inputs:
        id:                  Unique location identifier.
        name:                Human-readable name.
        kind:                Descriptive kind string (e.g. ``"studio"``, ``"exterior"``).
        day_rate:            Hire cost per shooting day.
        permit_windows:      Time ranges during which filming is permitted.
        supports_int_ext:    Subset of ``["INT", "EXT"]`` valid at this location.
        supports_time_of_day: Subset of ``["DAY","NIGHT","DAWN","DUSK"]`` valid here.
        address:             Street address for the call sheet, if known.
        parking_note:        Where the unit parks, for the call sheet.
        nearest_hospital:    Nearest A&E with a telephone number.

    The last three affect no rule, no cost and no plan — they are printed and
    nothing else. They live on the model rather than only in
    ``config/logistics.yaml`` because that file is keyed by the demo fixture's
    location ids and cannot know about an imported production's locations.

    Failure modes:
        Raises ``ValueError`` if ``day_rate < 0``.
    """

    id: str
    name: str
    kind: str
    day_rate: Decimal
    permit_windows: tuple[TimeWindow, ...]
    supports_int_ext: tuple[str, ...]
    supports_time_of_day: tuple[str, ...]
    address: str = ""
    parking_note: str = ""
    nearest_hospital: str = ""

    _quantise_rate = field_validator("day_rate", mode="before")(quantise_money)

    @model_validator(mode="after")
    def _non_negative_rate(self) -> Location:
        if self.day_rate < Decimal(0):
            raise ValueError(f"day_rate must be >= 0, got {self.day_rate}")
        return self


class Equipment(BaseModel, frozen=True):
    """A piece of equipment.

    Inputs:
        id:               Unique equipment identifier.
        name:             Human-readable name.
        kind:             Descriptive kind string (e.g. ``"camera"``, ``"crane"``).
        daily_rate:       Hire cost per day.
        available_windows: Time ranges when the equipment is available for use.

    Failure modes:
        Raises ``ValueError`` if ``daily_rate < 0``.
    """

    id: str
    name: str
    kind: str
    daily_rate: Decimal
    available_windows: tuple[TimeWindow, ...]

    _quantise_rate = field_validator("daily_rate", mode="before")(quantise_money)

    @model_validator(mode="after")
    def _non_negative_rate(self) -> Equipment:
        if self.daily_rate < Decimal(0):
            raise ValueError(f"daily_rate must be >= 0, got {self.daily_rate}")
        return self


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


class ShootingDay(BaseModel, frozen=True):
    """A single scheduled shooting day.

    Inputs:
        date:        Calendar date of the shoot.
        call_time:   Cast/crew call datetime (must be on ``date``'s calendar day
                     in the local timezone used by the production).
        wrap_time:   Expected wrap datetime; must be after ``call_time``.
        location_id: The primary location for this day.
        scene_ids:   Ordered list of scenes scheduled for this day.
        unit:        Production unit identifier; defaults to ``"MAIN"``.
        day_kind:    What the day is for. Only ``RESERVE`` days are available
                     to a recovery plan; a ``TRAVEL`` day has the unit on a bus
                     and a ``HOLD`` day is being kept for a reason PRI does not
                     know. Defaults to ``SHOOT``.

    Failure modes:
        Raises ``ValueError`` if ``wrap_time <= call_time``.
    """

    date: date
    call_time: datetime
    wrap_time: datetime
    location_id: str
    scene_ids: tuple[str, ...]
    unit: str = "MAIN"
    day_kind: DayKind = "SHOOT"

    @property
    def is_shoot(self) -> bool:
        """Whether the unit is actually filming. Non-shoot days skip C002."""
        return self.day_kind == "SHOOT"

    @property
    def is_available_for_scenes(self) -> bool:
        """Whether a recovery plan may put work here.

        Only reserve days. A travel day, a company move and a hold are all
        empty for reasons a scheduler must not overwrite.
        """
        return self.day_kind == "RESERVE"

    @model_validator(mode="after")
    def _wrap_after_call(self) -> ShootingDay:
        if self.wrap_time <= self.call_time:
            raise ValueError(
                f"wrap_time ({self.wrap_time}) must be after call_time ({self.call_time})"
            )
        return self


class Schedule(BaseModel, frozen=True):
    """The ordered collection of shooting days.

    Inputs:
        days: Shooting days in chronological order (not enforced here —
              the engine is responsible for ordering validation).
    """

    days: tuple[ShootingDay, ...]


# ---------------------------------------------------------------------------
# Versioned production state
# ---------------------------------------------------------------------------


class ProductionState(BaseModel, frozen=True):
    """Immutable snapshot of all production data at a specific version.

    Inputs:
        production:     The top-level production record.
        scenes:         All scenes keyed by index order.
        people:         All cast and crew.
        locations:      All locations.
        equipment:      All equipment.
        schedule:       The current shooting schedule.
        version:        Monotonically increasing integer; starts at 1.
        parent_version: The ``version`` of the state this was derived from;
                        ``None`` for the root state.
        event_id:       The ``DisruptionEvent.event_id`` that triggered this
                        version, if any.
        created_at:     UTC datetime when this version was created.

    Failure modes:
        Raises ``ValueError`` if ``version < 1`` or if
        ``parent_version`` is set but equals or exceeds ``version``.
    """

    production: Production
    scenes: tuple[Scene, ...]
    people: tuple[Person, ...]
    locations: tuple[Location, ...]
    equipment: tuple[Equipment, ...]
    schedule: Schedule
    version: int
    parent_version: int | None
    event_id: str | None
    created_at: datetime

    @model_validator(mode="after")
    def _version_invariants(self) -> ProductionState:
        if self.version < 1:
            raise ValueError(f"version must be >= 1, got {self.version}")
        if self.parent_version is not None and self.parent_version >= self.version:
            raise ValueError(
                f"parent_version ({self.parent_version}) must be < version ({self.version})"
            )
        return self

    # ------------------------------------------------------------------
    # State evolution
    # ------------------------------------------------------------------

    def apply(self, moves: list[Move]) -> ProductionState:
        """Apply a list of moves and return a new ``ProductionState``.

        This method is **mechanical only**: it performs no constraint checking.
        Validation is the exclusive responsibility of the engine layer.

        Inputs:
            moves: Ordered list of ``Move`` variants to apply in sequence.

        Outputs:
            A new ``ProductionState`` with:
            - ``version = self.version + 1``
            - ``parent_version = self.version``
            - ``created_at`` set to ``datetime.utcnow()``
            - ``event_id`` set to ``None`` (caller may override via ``model_copy``)
            - All schedule mutations accumulated from the moves.

        Failure modes:
            Raises ``KeyError`` if a move references a non-existent date or scene.
            Raises ``TypeError`` if an unknown ``Move`` kind is encountered.
        """
        # Work with mutable intermediate representations, then freeze at end.
        days_by_date: dict[date, ShootingDay] = {d.date: d for d in self.schedule.days}
        # scene_location map: scene_id -> location_id (mutable for RelocateScene)
        scene_location: dict[str, str] = {s.id: s.location_id for s in self.scenes}

        for move in moves:
            if isinstance(move, MoveSceneToDay):
                days_by_date = _apply_move_scene_to_day(move, days_by_date)

            elif isinstance(move, SwapDays):
                days_by_date = _apply_swap_days(move, days_by_date)

            elif isinstance(move, ShiftCallTime):
                days_by_date = _apply_shift_call_time(move, days_by_date)

            elif isinstance(move, RelocateScene):
                scene_location = _apply_relocate_scene(move, scene_location)

            else:
                # Exhaustiveness guard — should never fire with a correct union.
                raise TypeError(f"Unknown Move kind: {type(move)!r}")

        # Rebuild scenes with any location changes.
        new_scenes = tuple(
            s.model_copy(update={"location_id": scene_location[s.id]}) for s in self.scenes
        )

        days_by_date = _derive_day_locations(days_by_date, scene_location)
        new_schedule = Schedule(days=tuple(days_by_date[d] for d in sorted(days_by_date)))

        return ProductionState(
            production=self.production,
            scenes=new_scenes,
            people=self.people,
            locations=self.locations,
            equipment=self.equipment,
            schedule=new_schedule,
            version=self.version + 1,
            parent_version=self.version,
            event_id=None,
            created_at=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# Internal helpers for apply()  (module-private — not exported)
# ---------------------------------------------------------------------------


def _apply_move_scene_to_day(
    move: MoveSceneToDay,
    days: dict[date, ShootingDay],
) -> dict[date, ShootingDay]:
    """Remove ``scene_id`` from its current day and append it to ``target_date``.

    Inputs:
        move: A ``MoveSceneToDay`` instance.
        days: Mutable dict of shooting days keyed by date.

    Outputs:
        Updated dict with the scene relocated.

    Failure modes:
        Raises ``KeyError`` if ``target_date`` does not exist in ``days``.
        Raises ``ValueError`` if ``scene_id`` is not found on any day.
    """
    if move.target_date not in days:
        raise KeyError(f"MoveSceneToDay: target_date {move.target_date} is not a shooting day")

    # Remove from source day.
    source_day: ShootingDay | None = None
    for d in days.values():
        if move.scene_id in d.scene_ids:
            source_day = d
            break

    if source_day is None:
        raise ValueError(
            f"MoveSceneToDay: scene_id {move.scene_id!r} not found in any shooting day"
        )

    days[source_day.date] = source_day.model_copy(
        update={"scene_ids": tuple(s for s in source_day.scene_ids if s != move.scene_id)}
    )

    # Append to target day.
    target_day = days[move.target_date]
    days[move.target_date] = target_day.model_copy(
        update={"scene_ids": (*target_day.scene_ids, move.scene_id)}
    )
    return days


def _derive_day_locations(
    days: dict[date, ShootingDay],
    scene_location: dict[str, str],
) -> dict[date, ShootingDay]:
    """Follow the scenes when a whole day's work has relocated.

    A shooting day is booked at the place its scenes are shot, so relocating
    every scene on a day relocates the day: the unit is no longer standing at
    the old address, and it is the new location's permit that governs the
    window.  When a day's scenes disagree about where they are — a deferred
    scene landing on a reserve day already holding other work — the day keeps
    the location it was booked at, because something still has to be booked.

    Inputs:
        days:           Shooting days keyed by date.
        scene_location: The post-move mapping of scene_id to location_id.

    Outputs:
        The same dict, with unanimous days re-pointed at their scenes' location.
    """
    for day_date, day in days.items():
        locations = {scene_location[sid] for sid in day.scene_ids if sid in scene_location}
        if len(locations) == 1:
            (only,) = locations
            if only != day.location_id:
                days[day_date] = day.model_copy(update={"location_id": only})
    return days


def _rebase_day(day: ShootingDay, target: date) -> tuple[datetime, datetime]:
    """Re-anchor a day's call/wrap clock times onto a different calendar date.

    The local clock time of the call and the day's total duration are both
    preserved, so a day that wraps after midnight keeps doing so.

    Inputs:
        day:    The shooting day whose times are being moved.
        target: The calendar date the times should land on.

    Outputs:
        A ``(call_time, wrap_time)`` tuple anchored to ``target``.
    """
    tz = day.call_time.tzinfo
    origin = datetime.combine(day.date, time.min, tzinfo=tz)
    call_offset = day.call_time - origin
    duration = day.wrap_time - day.call_time
    new_call = datetime.combine(target, time.min, tzinfo=tz) + call_offset
    return new_call, new_call + duration


def _apply_swap_days(
    move: SwapDays,
    days: dict[date, ShootingDay],
) -> dict[date, ShootingDay]:
    """Exchange the entire contents of ``date_a`` and ``date_b``.

    A day swap in production means the whole shooting day moves: its location,
    its call and wrap times, and its scenes.  Only the calendar date stays put.
    Swapping scenes alone would leave scenes stranded at a location that cannot
    host them and would silently preserve the original turnaround, which is
    exactly the constraint the swap is supposed to stress.

    Call and wrap times are re-anchored onto their new date by
    :func:`_rebase_day`, preserving each day's local clock times and duration.

    Inputs:
        move: A ``SwapDays`` instance.
        days: Mutable dict of shooting days keyed by date.

    Outputs:
        Updated dict with the two days' content exchanged.

    Failure modes:
        Raises ``KeyError`` if either date is not a shooting day.
    """
    for d in (move.date_a, move.date_b):
        if d not in days:
            raise KeyError(f"SwapDays: {d} is not a shooting day")

    day_a = days[move.date_a]
    day_b = days[move.date_b]

    a_call, a_wrap = _rebase_day(day_b, move.date_a)
    b_call, b_wrap = _rebase_day(day_a, move.date_b)

    days[move.date_a] = day_a.model_copy(
        update={
            "call_time": a_call,
            "wrap_time": a_wrap,
            "location_id": day_b.location_id,
            "scene_ids": day_b.scene_ids,
        }
    )
    days[move.date_b] = day_b.model_copy(
        update={
            "call_time": b_call,
            "wrap_time": b_wrap,
            "location_id": day_a.location_id,
            "scene_ids": day_a.scene_ids,
        }
    )
    return days


def _apply_shift_call_time(
    move: ShiftCallTime,
    days: dict[date, ShootingDay],
) -> dict[date, ShootingDay]:
    """Change the ``call_time`` of a shooting day, holding ``wrap_time`` fixed.

    Wrap is deliberately *not* shifted with the call.  A later call is how a
    schedule buys crew turnaround, and the wrap is pinned by things the move
    cannot negotiate — a location permit expiring, the light going.  Shifting
    both would preserve the day's length by pushing the wrap past those limits,
    trading a turnaround violation for a permit violation.  The day therefore
    gets shorter, and it is the candidate generator's job to shed scenes until
    the remaining work fits.

    Inputs:
        move: A ``ShiftCallTime`` instance.
        days: Mutable dict of shooting days keyed by date.

    Outputs:
        Updated dict with the new call time and the original wrap time.

    Failure modes:
        Raises ``KeyError`` if ``move.date`` is not a shooting day.
        Raises ``pydantic.ValidationError`` if the new call time is at or after
        the day's wrap time.
    """
    if move.date not in days:
        raise KeyError(f"ShiftCallTime: {move.date} is not a shooting day")

    day = days[move.date]
    days[move.date] = day.model_copy(update={"call_time": move.new_call_time})
    return days


def _apply_relocate_scene(
    move: RelocateScene,
    scene_location: dict[str, str],
) -> dict[str, str]:
    """Update the location assigned to a scene.

    Inputs:
        move:           A ``RelocateScene`` instance.
        scene_location: Mutable dict mapping scene_id -> location_id.

    Outputs:
        Updated dict with the new location assigned.

    Failure modes:
        Raises ``KeyError`` if ``scene_id`` is not in the map.
    """
    if move.scene_id not in scene_location:
        raise KeyError(f"RelocateScene: scene_id {move.scene_id!r} not found in production")
    scene_location[move.scene_id] = move.target_location_id
    return scene_location


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class DisruptionEvent(BaseModel, frozen=True):
    """An external event that disrupts the production.

    Inputs:
        event_id:      Unique event identifier (UUID string recommended).
        production_id: The production this event affects.
        event_type:    Structured disruption category.
        occurred_at:   When the disruption occurred or was reported.
        source:        Free-text description of the event source.
        severity:      Numeric severity in ``[0.0, 1.0]``; 1.0 = most severe.
        payload:       Event-type-specific key/value data.

    Failure modes:
        Raises ``ValueError`` if ``severity`` is outside ``[0.0, 1.0]``.
    """

    event_id: str
    production_id: str
    event_type: Literal[
        "location.blocked",
        "actor.unavailable",
        "equipment.failed",
        "weather.changed",
        "crew.unavailable",
    ]
    occurred_at: datetime
    source: str
    severity: float
    payload: dict[str, object]

    @model_validator(mode="after")
    def _severity_range(self) -> DisruptionEvent:
        if not (0.0 <= self.severity <= 1.0):
            raise ValueError(f"severity must be in [0.0, 1.0], got {self.severity}")
        return self


# ---------------------------------------------------------------------------
# Move discriminated union
# ---------------------------------------------------------------------------


class MoveSceneToDay(BaseModel, frozen=True):
    """Move a scene from its current shooting day to another.

    Inputs:
        scene_id:    The scene to relocate.
        target_date: The new shooting day.
    """

    kind: Literal["MoveSceneToDay"] = "MoveSceneToDay"
    scene_id: str
    target_date: date


class SwapDays(BaseModel, frozen=True):
    """Exchange the scene lists of two shooting days.

    Inputs:
        date_a: First day.
        date_b: Second day.
    """

    kind: Literal["SwapDays"] = "SwapDays"
    date_a: date
    date_b: date


class ShiftCallTime(BaseModel, frozen=True):
    """Shift the call time (and wrap time) of a shooting day.

    Inputs:
        date:          The shooting day to modify.
        new_call_time: The replacement call time.
    """

    kind: Literal["ShiftCallTime"] = "ShiftCallTime"
    date: date
    new_call_time: datetime


class RelocateScene(BaseModel, frozen=True):
    """Assign a scene to a different location.

    Inputs:
        scene_id:           The scene to reassign.
        target_location_id: The replacement location.
    """

    kind: Literal["RelocateScene"] = "RelocateScene"
    scene_id: str
    target_location_id: str


Move = Annotated[
    MoveSceneToDay | SwapDays | ShiftCallTime | RelocateScene,
    Field(discriminator="kind"),
]

# ---------------------------------------------------------------------------
# Plans and evaluation
# ---------------------------------------------------------------------------


class CandidatePlan(BaseModel, frozen=True):
    """A proposed recovery plan consisting of an ordered sequence of moves.

    Inputs:
        id:             Unique plan identifier (UUID string recommended).
        label:          Short human-readable label.
        base_version:   The ``ProductionState.version`` this plan was built on.
        moves:          Ordered list of moves to apply.
        rationale_hint: Optional free-text hint for the AI narration layer;
                        numbers are NOT derived from this string.
    """

    id: str
    label: str
    base_version: int
    moves: tuple[Move, ...]
    rationale_hint: str | None


class ConstraintViolation(BaseModel, frozen=True):
    """A single constraint that a plan violates.

    Inputs:
        code:        Machine-readable violation code (e.g. ``"LOC_UNAVAILABLE"``).
        severity:    ``"HARD"`` violations block approval; ``"SOFT"`` are warnings.
        message:     Human-readable description.
        subject_ids: IDs of entities involved in the violation.
        observed:    String representation of the observed value.
        required:    String representation of the required value.
    """

    code: str
    severity: Literal["HARD", "SOFT"]
    message: str
    subject_ids: tuple[str, ...]
    observed: str
    required: str


class PlanScore(BaseModel, frozen=True):
    """Deterministically computed quality metrics for a candidate plan.

    All values are computed by the engine, never by an LLM.

    Inputs:
        schedule_delay_days:         Total additional shoot days introduced.
        incremental_cost:            Extra cost vs the baseline in production currency.
        operational_risk:            Composite risk score in ``[0.0, 1.0]``.
        affected_scene_count:        Number of scenes whose date/location changed.
        crew_disruption_hours:       Total crew idle / travel hours added.
        downstream_dependency_impact: Number of dependent scenes affected.
    """

    schedule_delay_days: float
    incremental_cost: Decimal
    operational_risk: float
    affected_scene_count: int
    crew_disruption_hours: float
    downstream_dependency_impact: int


class EvaluatedPlan(BaseModel, frozen=True):
    """A candidate plan after deterministic validation and scoring.

    Inputs:
        plan:                  The ``CandidatePlan`` that was evaluated.
        valid:                 ``True`` iff there are no HARD violations.
        violations:            All constraint violations found (HARD and SOFT).
        score:                 Computed metrics; ``None`` if validation failed
                               before scoring could complete.
        resulting_state_digest: SHA-256 hex digest of the resulting
                                ``ProductionState`` JSON; ``None`` if scoring
                                did not complete.
        pareto_optimal:        ``True`` if this plan is on the Pareto frontier
                               of (cost, delay, risk).  Defaults to ``False``;
                               set by the engine after comparing all plans.
    """

    plan: CandidatePlan
    valid: bool
    violations: tuple[ConstraintViolation, ...]
    score: PlanScore | None
    resulting_state_digest: str | None
    pareto_optimal: bool = False


# ---------------------------------------------------------------------------
# Impact analysis result
# ---------------------------------------------------------------------------


class ImpactReport(BaseModel, frozen=True):
    """Deterministically computed blast-radius report for a disruption event.

    All values are computed by the engine graph layer — never by an LLM.

    Inputs:
        event_id:                    The ``DisruptionEvent.event_id`` analysed.
        directly_affected_scene_ids: Scenes directly blocked by the event.
        downstream_scene_ids:        Scenes that cannot shoot because a
                                     prerequisite is in the directly-affected set.
        affected_cast_ids:           Person IDs (role=CAST) on affected scenes.
        affected_crew_ids:           Person IDs (role=CREW) on affected scenes.
        affected_location_ids:       Location IDs on affected scenes.
        affected_equipment_ids:      Equipment IDs on affected scenes.
        affected_days:               Shooting-day dates that contain at least one
                                     affected scene.
        downstream_dependency_count: Number of distinct downstream scenes.
        blast_radius:                ``len(all affected) / len(remaining scenes)``
                                     in ``[0.0, 1.0]``.  0.0 when no scenes remain.
    """

    event_id: str
    directly_affected_scene_ids: tuple[str, ...]
    downstream_scene_ids: tuple[str, ...]
    affected_cast_ids: tuple[str, ...]
    affected_crew_ids: tuple[str, ...]
    affected_location_ids: tuple[str, ...]
    affected_equipment_ids: tuple[str, ...]
    affected_days: tuple[date, ...]
    downstream_dependency_count: int
    blast_radius: float


# ---------------------------------------------------------------------------
# Recovery loop trace
# ---------------------------------------------------------------------------


class RoundTrace(BaseModel, frozen=True):
    """What one round of the recovery loop generated, and what it learned.

    The UI replays these as the recovery timeline, so the wording of ``note``
    reaches the screen: it is a summary line, not a debug string.

    Inputs:
        round_number:       1-based round index.
        strategy_hints:     The families this round was asked to expand.
        generated_plan_ids: Every plan produced this round, in generation order.
        invalid_plan_ids:   The subset that failed deterministic validation.
        failed_rule_codes:  Distinct rule codes that caused those failures,
                            in ascending order (e.g. ``("C001",)``).
        repaired_plan_ids:  Repair plans this round produced for the failures.
        note:               One-line human summary of the round.
    """

    round_number: int
    strategy_hints: tuple[str, ...]
    generated_plan_ids: tuple[str, ...]
    invalid_plan_ids: tuple[str, ...]
    failed_rule_codes: tuple[str, ...]
    repaired_plan_ids: tuple[str, ...]
    note: str


class RecoveryResult(BaseModel, frozen=True):
    """The complete output of one recovery run.

    Inputs:
        session_id:    Identifier for this recovery session.
        production_id: The production being recovered.
        event_id:      The disruption that triggered the run.
        base_version:  The state version every candidate was built on.
        impact:        The blast-radius report the candidates responded to.
        evaluated:     Every candidate, validated and scored, in generation
                       order.  Invalid candidates are retained deliberately —
                       the rejected plan is the evidence that validation is
                       real.
        rounds:        One trace per round of the loop.
    """

    session_id: str
    production_id: str
    event_id: str
    base_version: int
    impact: ImpactReport
    evaluated: tuple[EvaluatedPlan, ...]
    rounds: tuple[RoundTrace, ...]

    @property
    def valid_plans(self) -> tuple[EvaluatedPlan, ...]:
        """Every candidate that passed deterministic validation."""
        return tuple(ep for ep in self.evaluated if ep.valid)

    @property
    def pareto_plans(self) -> tuple[EvaluatedPlan, ...]:
        """The non-dominated valid candidates — what the producer chooses between."""
        return tuple(ep for ep in self.evaluated if ep.pareto_optimal)


# ---------------------------------------------------------------------------
# Factory helpers (module-private convenience — not exported)
# ---------------------------------------------------------------------------


def _new_id() -> str:
    """Return a fresh UUID4 string."""
    return str(uuid.uuid4())
