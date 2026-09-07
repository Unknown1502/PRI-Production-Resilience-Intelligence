"""Constraint validator for PRI.

Deterministically checks every structural and policy invariant of a
``ProductionState``.  No LLM, no solver, no approximation — only exact
arithmetic on ``datetime`` and ``Decimal``.

Architecture laws:
- ``validate(state)`` is a pure function: no IO, no side-effects.
- Policies are loaded once from ``config/policies.yaml`` at import time via
  :func:`load_policy`; override with :func:`validate` ``policy=`` kwarg for
  tests.
- Every rule is a standalone function registered in :data:`RULES`.  The UI
  shows the rule code and human-readable ``observed`` / ``required`` strings.

Rule registry:
    C001  crew_turnaround      wrap[day-n] → call[day-n+1] ≥ minimum_hours
    C002  max_daily_hours      wrap - call <= maximum_daily_hours
    C003  cast_availability    no scene inside a cast member's unavailable_window
    C004  location_permit      shooting day fits inside permit_window(s)
    C005  location_double_book two units at one location on the same day
    C006  equipment_window     equipment used outside its available_window
    C007  equipment_double_book same equipment on two units on the same day
    C008  prerequisite_order   prerequisite scene scheduled strictly earlier
    C009  int_ext_match        scene INT/EXT supported by its location
    C010  time_of_day_match    scene time_of_day supported by its location
"""

from __future__ import annotations

import functools
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

import yaml

from pri.domain.models import ConstraintViolation
from pri.paths import config_path

if TYPE_CHECKING:
    from pathlib import Path

    from pri.domain.models import (
        Equipment,
        Location,
        Person,
        ProductionState,
        ShootingDay,
        TimeWindow,
    )

# ---------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------

_DEFAULT_POLICY_PATH = config_path("policies.yaml")

Policy = dict[str, Any]  # flat/nested dict parsed from YAML


def load_policy(path: Path | None = None) -> Policy:
    """Load the policy YAML file and return it as a plain dict.

    Inputs:
        path: Override the default ``config/policies.yaml`` path (used in tests).

    Outputs:
        A :class:`Policy` dict matching the structure of ``policies.yaml``.

    Failure modes:
        Raises ``FileNotFoundError`` if the file is missing.
        Raises ``yaml.YAMLError`` if the file is malformed.
    """
    resolved = path or _DEFAULT_POLICY_PATH
    with resolved.open() as fh:
        data: Policy = yaml.safe_load(fh)
    return data


@functools.lru_cache(maxsize=1)
def _cached_policy() -> Policy:
    """Return the module-level cached policy (loaded once at runtime)."""
    return load_policy()


# ---------------------------------------------------------------------------
# Rule type
# ---------------------------------------------------------------------------

RuleFunc = Callable[["ProductionState", Policy], list[ConstraintViolation]]


# ---------------------------------------------------------------------------
# Helper constructors
# ---------------------------------------------------------------------------


def _v(
    code: str,
    severity: str,
    message: str,
    subject_ids: tuple[str, ...],
    observed: str,
    required: str,
) -> ConstraintViolation:
    """Shorthand to build a :class:`ConstraintViolation`."""
    sev: Literal["HARD", "SOFT"] = "HARD" if severity == "HARD" else "SOFT"
    return ConstraintViolation(
        code=code,
        severity=sev,
        message=message,
        subject_ids=subject_ids,
        observed=observed,
        required=required,
    )


def _hours(td: timedelta) -> float:
    """Convert a ``timedelta`` to a float number of hours (exact)."""
    return td.total_seconds() / 3600.0


def _fmt_hours(td: timedelta) -> str:
    """Format a ``timedelta`` as e.g. ``"9.0h"``."""
    h = _hours(td)
    # Display as integer if whole, else one decimal place.
    return f"{h:.1f}h"


def _windows_for(windows: tuple[TimeWindow, ...], day: ShootingDay) -> bool:
    """Whether the day's ``[call_time, wrap_time)`` *overlaps* any window.

    Overlap, not containment — the question this answers is "is this resource
    touched at all on this day", which is what an availability window asks.

    Inputs:
        windows: The entity's windows. Empty means no restriction.
        day:     The shooting day to test.

    Outputs:
        ``True`` when the day overlaps at least one window, or when there are
        no windows at all.

    Failure modes:
        Does not raise.
    """
    if not windows:
        return True
    # Half-open overlap: start1 < end2 and start2 < end1
    return any(day.call_time < w.end and w.start < day.wrap_time for w in windows)


def _day_fits_in_windows(windows: tuple[TimeWindow, ...], day: ShootingDay) -> bool:
    """Whether the day's ``[call_time, wrap_time)`` is *fully contained* in a window.

    Containment, not overlap — a permit that expires at 19:00 is not satisfied
    by a day that merely starts before then.

    Inputs:
        windows: The permitted windows. Empty means no restriction.
        day:     The shooting day to test.

    Outputs:
        ``True`` when the whole day sits inside at least one window, or when
        there are no windows at all.

    Failure modes:
        Does not raise.
    """
    if not windows:
        return True
    return any(w.start <= day.call_time and day.wrap_time <= w.end for w in windows)


# ---------------------------------------------------------------------------
# C001 — crew_turnaround
# ---------------------------------------------------------------------------


def check_crew_turnaround(state: ProductionState, policy: Policy) -> list[ConstraintViolation]:
    """C001: Rest between wrap of day-N and call of day-(N+1) >= minimum_hours.

    Inputs:
        state:  Current production state.
        policy: Loaded policy dict.

    Outputs:
        A violation for every consecutive day pair where the gap is too short.

    Failure modes:
        Does not raise; returns [] on empty schedule.
    """
    ct_policy = policy.get("crew_turnaround", {})
    minimum_hours: float = float(ct_policy.get("minimum_hours", 10.0))
    minimum_td = timedelta(hours=minimum_hours)

    violations: list[ConstraintViolation] = []
    days = sorted(state.schedule.days, key=lambda d: d.date)

    for i in range(len(days) - 1):
        day_a = days[i]
        day_b = days[i + 1]
        if day_a.unit != day_b.unit:
            continue
        gap = day_b.call_time - day_a.wrap_time
        if gap < minimum_td:
            violations.append(
                _v(
                    code="C001",
                    severity="HARD",
                    message=(
                        f"Insufficient crew turnaround between "
                        f"{day_a.date} and {day_b.date}: "
                        f"{_fmt_hours(gap)} rest is less than {minimum_hours}h minimum."
                    ),
                    subject_ids=(day_a.date.isoformat(), day_b.date.isoformat()),
                    observed=_fmt_hours(gap),
                    required=f">= {minimum_hours}h",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# C002 — max_daily_hours
# ---------------------------------------------------------------------------


def check_max_daily_hours(state: ProductionState, policy: Policy) -> list[ConstraintViolation]:
    """C002: Each shooting day duration <= maximum_daily_hours.

    Inputs:
        state:  Current production state.
        policy: Loaded policy dict.

    Outputs:
        A violation for every **shooting** day that exceeds the maximum.

        Non-shoot days are skipped. A travel day's call-to-wrap span is a
        coach departure and an arrival, not a working day, and a fourteen-hour
        drive is not a breach of a shooting-hours rule. Applying the rule to
        those rows produced violations a producer could do nothing about.

    Failure modes:
        Does not raise; returns [] on empty schedule.
    """
    sh_policy = policy.get("shooting", {})
    max_hours: float = float(sh_policy.get("maximum_daily_hours", 12.0))
    max_td = timedelta(hours=max_hours)

    violations: list[ConstraintViolation] = []
    for day in state.schedule.days:
        if not day.is_shoot:
            continue
        duration = day.wrap_time - day.call_time
        if duration > max_td:
            violations.append(
                _v(
                    code="C002",
                    severity="HARD",
                    message=(
                        f"Day {day.date} exceeds maximum shooting hours: "
                        f"{_fmt_hours(duration)} > {max_hours}h."
                    ),
                    subject_ids=(day.date.isoformat(),),
                    observed=_fmt_hours(duration),
                    required=f"<= {max_hours}h",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# C003 — cast_availability
# ---------------------------------------------------------------------------


def check_cast_availability(state: ProductionState, policy: Policy) -> list[ConstraintViolation]:
    """C003: No scene scheduled inside a cast member's unavailable_window.

    Only fires if ``cast.unavailable_dates_are_hard`` is True in policy.

    Inputs:
        state:  Current production state.
        policy: Loaded policy dict.

    Outputs:
        A violation for every (person, day, scene) triple that conflicts.

    Failure modes:
        Does not raise; returns [] if policy flag is off or schedule is empty.
    """
    cast_policy = policy.get("cast", {})
    if not cast_policy.get("unavailable_dates_are_hard", True):
        return []

    person_map: dict[str, Person] = {p.id: p for p in state.people}
    scene_map = {s.id: s for s in state.scenes}

    violations: list[ConstraintViolation] = []

    for day in state.schedule.days:
        for scene_id in day.scene_ids:
            scene = scene_map.get(scene_id)
            if scene is None:
                continue
            for pid in scene.cast_ids:
                person = person_map.get(pid)
                if person is None:
                    continue
                for window in person.unavailable_windows:
                    # Conflict: shooting day overlaps with unavailability window
                    if day.call_time < window.end and window.start < day.wrap_time:
                        violations.append(
                            _v(
                                code="C003",
                                severity="HARD",
                                message=(
                                    f"{person.name} is unavailable on {day.date} "
                                    f"but is scheduled for scene {scene_id}."
                                ),
                                subject_ids=(pid, scene_id, day.date.isoformat()),
                                observed=(
                                    f"{day.date} "
                                    f"({day.call_time.isoformat()} to {day.wrap_time.isoformat()})"
                                ),
                                required=(
                                    f"outside window "
                                    f"{window.start.isoformat()} to {window.end.isoformat()}"
                                ),
                            )
                        )
    return violations


# ---------------------------------------------------------------------------
# C004 — location_permit
# ---------------------------------------------------------------------------


def check_location_permit(state: ProductionState, policy: Policy) -> list[ConstraintViolation]:
    """C004: Shooting day window must be fully contained in a location permit window.

    Only fires if ``location.respect_permit_windows`` is True and the location
    has at least one permit window defined.

    Inputs:
        state:  Current production state.
        policy: Loaded policy dict.

    Outputs:
        A violation for every day whose window does not fit a permit window.

    Failure modes:
        Does not raise; skips locations with no permit windows (no constraint).
    """
    loc_policy = policy.get("location", {})
    if not loc_policy.get("respect_permit_windows", True):
        return []

    loc_map: dict[str, Location] = {loc.id: loc for loc in state.locations}
    violations: list[ConstraintViolation] = []

    for day in state.schedule.days:
        loc = loc_map.get(day.location_id)
        if loc is None or not loc.permit_windows:
            continue  # no windows defined → no constraint
        if not _day_fits_in_windows(loc.permit_windows, day):
            windows_str = ", ".join(
                f"{w.start.isoformat()} to {w.end.isoformat()}" for w in loc.permit_windows
            )
            violations.append(
                _v(
                    code="C004",
                    severity="HARD",
                    message=(
                        f"Day {day.date} at location '{loc.name}' falls outside all permit windows."
                    ),
                    subject_ids=(day.date.isoformat(), loc.id),
                    observed=f"{day.call_time.isoformat()} to {day.wrap_time.isoformat()}",
                    required=f"inside one of: {windows_str}",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# C005 — location_double_book
# ---------------------------------------------------------------------------


def check_location_double_book(state: ProductionState, policy: Policy) -> list[ConstraintViolation]:
    """C005: Two units may not use the same location on the same calendar date.

    Only fires if ``location.no_double_booking`` is True.

    Inputs:
        state:  Current production state.
        policy: Loaded policy dict.

    Outputs:
        A violation for every (location, date) pair claimed by more than one unit.

    Failure modes:
        Does not raise; returns [] on empty schedule.
    """
    loc_policy = policy.get("location", {})
    if not loc_policy.get("no_double_booking", True):
        return []

    # (location_id, date) -> list[unit]
    booking: dict[tuple[str, str], list[str]] = defaultdict(list)
    for day in state.schedule.days:
        booking[(day.location_id, day.date.isoformat())].append(day.unit)

    violations: list[ConstraintViolation] = []
    for (loc_id, date_str), units in booking.items():
        if len(units) > 1:
            violations.append(
                _v(
                    code="C005",
                    severity="HARD",
                    message=(f"Location {loc_id!r} is double-booked on {date_str}: units {units}."),
                    subject_ids=(loc_id, date_str),
                    observed=f"units {sorted(units)}",
                    required="single unit per location per day",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# C006 — equipment_window
# ---------------------------------------------------------------------------


def check_equipment_window(state: ProductionState, policy: Policy) -> list[ConstraintViolation]:
    """C006: Equipment must not be used outside its available_windows.

    Only fires if ``equipment.respect_rental_windows`` is True and the
    equipment piece has at least one window defined.

    Inputs:
        state:  Current production state.
        policy: Loaded policy dict.

    Outputs:
        A violation for every (equipment, day) pair outside the rental window.

    Failure modes:
        Does not raise; skips equipment with no windows (no constraint).
    """
    eq_policy = policy.get("equipment", {})
    if not eq_policy.get("respect_rental_windows", True):
        return []

    equip_map: dict[str, Equipment] = {e.id: e for e in state.equipment}
    scene_map = {s.id: s for s in state.scenes}
    violations: list[ConstraintViolation] = []

    for day in state.schedule.days:
        # Collect all equipment IDs needed on this day
        needed: set[str] = set()
        for scene_id in day.scene_ids:
            scene = scene_map.get(scene_id)
            if scene:
                needed.update(scene.equipment_ids)

        for eid in needed:
            equip = equip_map.get(eid)
            if equip is None or not equip.available_windows:
                continue
            if not _day_fits_in_windows(equip.available_windows, day):
                windows_str = ", ".join(
                    f"{w.start.isoformat()} to {w.end.isoformat()}" for w in equip.available_windows
                )
                violations.append(
                    _v(
                        code="C006",
                        severity="HARD",
                        message=(
                            f"Equipment '{equip.name}' used on {day.date} "
                            f"outside its available rental windows."
                        ),
                        subject_ids=(eid, day.date.isoformat()),
                        observed=f"{day.call_time.isoformat()} to {day.wrap_time.isoformat()}",
                        required=f"inside one of: {windows_str}",
                    )
                )
    return violations


# ---------------------------------------------------------------------------
# C007 — equipment_double_book
# ---------------------------------------------------------------------------


def check_equipment_double_book(
    state: ProductionState, policy: Policy
) -> list[ConstraintViolation]:
    """C007: Same equipment on two units on the same calendar date.

    Only fires if ``equipment.no_double_booking`` is True.

    Inputs:
        state:  Current production state.
        policy: Loaded policy dict.

    Outputs:
        A violation for every (equipment_id, date) pair claimed by > 1 unit.

    Failure modes:
        Does not raise; returns [] on empty schedule.
    """
    eq_policy = policy.get("equipment", {})
    if not eq_policy.get("no_double_booking", True):
        return []

    scene_map = {s.id: s for s in state.scenes}
    # (equip_id, date) -> list[unit]
    booking: dict[tuple[str, str], list[str]] = defaultdict(list)

    for day in state.schedule.days:
        seen_equip: set[str] = set()
        for scene_id in day.scene_ids:
            scene = scene_map.get(scene_id)
            if scene:
                seen_equip.update(scene.equipment_ids)
        for eid in seen_equip:
            booking[(eid, day.date.isoformat())].append(day.unit)

    violations: list[ConstraintViolation] = []
    for (eid, date_str), units in booking.items():
        if len(units) > 1:
            violations.append(
                _v(
                    code="C007",
                    severity="HARD",
                    message=(
                        f"Equipment {eid!r} is double-booked on {date_str}: units {sorted(units)}."
                    ),
                    subject_ids=(eid, date_str),
                    observed=f"units {sorted(units)}",
                    required="single unit per equipment piece per day",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# C008 — prerequisite_order
# ---------------------------------------------------------------------------


def check_prerequisite_order(state: ProductionState, policy: Policy) -> list[ConstraintViolation]:
    """C008: Every prerequisite must be scheduled on a strictly earlier date.

    Strictly earlier, not "earlier or the same day".  A prerequisite shot on the
    same day is a prerequisite the schedule cannot guarantee: the shot list can
    be reordered on the morning, weather can flip the running order, and a
    dependency that survives only because of the order two slugs happen to sit
    in is not a dependency the production can rely on.

    Only fires if ``dependencies.preserve_prerequisite_order`` is True.

    Inputs:
        state:  Current production state.
        policy: Loaded policy dict.

    Outputs:
        A violation for every (scene, prerequisite) pair scheduled on the same
        date or in the wrong order.

    Failure modes:
        Does not raise; skips scenes with no prerequisites and pairs where
        either scene is unscheduled.
    """
    dep_policy = policy.get("dependencies", {})
    if not dep_policy.get("preserve_prerequisite_order", True):
        return []

    scene_date: dict[str, str] = {}
    for day in state.schedule.days:
        for sid in day.scene_ids:
            scene_date[sid] = day.date.isoformat()

    violations: list[ConstraintViolation] = []
    for scene in state.scenes:
        on = scene_date.get(scene.id)
        if on is None:
            continue
        for prereq_id in scene.prerequisite_scene_ids:
            prereq_on = scene_date.get(prereq_id)
            if prereq_on is None:
                continue  # an unscheduled prerequisite is a different problem
            if on > prereq_on:
                continue
            same_day = on == prereq_on
            observed = (
                f"{scene.id} on {on}, {prereq_id} also on {prereq_on}"
                if same_day
                else f"{scene.id} on {on}, {prereq_id} not until {prereq_on}"
            )
            message = (
                f"Scene {scene.id} is scheduled on the same day as its prerequisite "
                f"{prereq_id}; the prerequisite must be shot on an earlier date."
                if same_day
                else f"Scene {scene.id} is scheduled before its prerequisite {prereq_id}."
            )
            violations.append(
                _v(
                    code="C008",
                    severity="HARD",
                    message=message,
                    subject_ids=(scene.id, prereq_id),
                    observed=observed,
                    required=f"{prereq_id} on earlier date",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# C009 — int_ext_match
# ---------------------------------------------------------------------------


def check_int_ext_match(state: ProductionState, policy: Policy) -> list[ConstraintViolation]:
    """C009: Scene INT/EXT must be in the location's ``supports_int_ext`` list.

    Only fires if ``scene_environment.enforce_int_ext_match`` is True.

    Inputs:
        state:  Current production state.
        policy: Loaded policy dict.

    Outputs:
        A violation for every scene whose INT/EXT is not supported.

    Failure modes:
        Does not raise; skips locations with empty supports_int_ext (no constraint).
    """
    env_policy = policy.get("scene_environment", {})
    if not env_policy.get("enforce_int_ext_match", True):
        return []

    loc_map: dict[str, Location] = {loc.id: loc for loc in state.locations}
    violations: list[ConstraintViolation] = []

    for scene in state.scenes:
        loc = loc_map.get(scene.location_id)
        if loc is None or not loc.supports_int_ext:
            continue
        if scene.int_ext not in loc.supports_int_ext:
            violations.append(
                _v(
                    code="C009",
                    severity="HARD",
                    message=(
                        f"Scene {scene.id!r} requires {scene.int_ext!r} but location "
                        f"'{loc.name}' only supports {list(loc.supports_int_ext)}."
                    ),
                    subject_ids=(scene.id, loc.id),
                    observed=scene.int_ext,
                    required=f"one of {list(loc.supports_int_ext)}",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# C010 — time_of_day_match
# ---------------------------------------------------------------------------


def check_time_of_day_match(state: ProductionState, policy: Policy) -> list[ConstraintViolation]:
    """C010: Scene time_of_day must be in the location's ``supports_time_of_day``.

    Only fires if ``scene_environment.enforce_time_of_day_match`` is True.

    Inputs:
        state:  Current production state.
        policy: Loaded policy dict.

    Outputs:
        A violation for every scene whose time_of_day is not supported.

    Failure modes:
        Does not raise; skips locations with empty supports_time_of_day.
    """
    env_policy = policy.get("scene_environment", {})
    if not env_policy.get("enforce_time_of_day_match", True):
        return []

    loc_map: dict[str, Location] = {loc.id: loc for loc in state.locations}
    violations: list[ConstraintViolation] = []

    for scene in state.scenes:
        loc = loc_map.get(scene.location_id)
        if loc is None or not loc.supports_time_of_day:
            continue
        if scene.time_of_day not in loc.supports_time_of_day:
            violations.append(
                _v(
                    code="C010",
                    severity="HARD",
                    message=(
                        f"Scene {scene.id!r} requires time_of_day={scene.time_of_day!r} "
                        f"but location '{loc.name}' only supports "
                        f"{list(loc.supports_time_of_day)}."
                    ),
                    subject_ids=(scene.id, loc.id),
                    observed=scene.time_of_day,
                    required=f"one of {list(loc.supports_time_of_day)}",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

#: Every rule, in code order. Adding a rule is a one-line registration here,
#: not a rewrite of :func:`validate`.
RULES: list[RuleFunc] = [
    check_crew_turnaround,
    check_max_daily_hours,
    check_cast_availability,
    check_location_permit,
    check_location_double_book,
    check_equipment_window,
    check_equipment_double_book,
    check_prerequisite_order,
    check_int_ext_match,
    check_time_of_day_match,
]

#: The rule code each entry in :data:`RULES` emits, in the same order.
RULE_CODES: tuple[str, ...] = (
    "C001",
    "C002",
    "C003",
    "C004",
    "C005",
    "C006",
    "C007",
    "C008",
    "C009",
    "C010",
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate(
    state: ProductionState,
    *,
    policy: Policy | None = None,
) -> list[ConstraintViolation]:
    """Run all registered constraint rules against a ``ProductionState``.

    This function is **pure**: no IO, no side-effects.  The same inputs always
    produce the same output.  No LLM is involved.

    Inputs:
        state:  The state to validate.
        policy: Override the module-level cached policy (useful in tests).
                When ``None``, the default ``config/policies.yaml`` is used.

    Outputs:
        A list of :class:`~pri.domain.models.ConstraintViolation` objects,
        one per violation found.  Empty list means fully valid.  The list is
        ordered by rule code (C001 … C010).

    Failure modes:
        Does not raise for missing entities in cross-references; individual rule
        functions skip unresolvable references silently.
        Raises ``FileNotFoundError`` / ``yaml.YAMLError`` on first call if the
        policy file is missing or malformed (and no override is supplied).
    """
    resolved_policy: Policy = policy if policy is not None else _cached_policy()
    violations: list[ConstraintViolation] = []
    for rule in RULES:
        violations.extend(rule(state, resolved_policy))
    return violations
