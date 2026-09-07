"""Candidate recovery plan generation.

This module is the **only** place a :class:`CandidatePlan` is created.  Gemini
supplies ``strategy_hints`` — which families to expand and in what order — and
nothing else.  It cannot author a move, reorder a schedule, or introduce a plan
that did not come out of one of the four enumerated families below.  That
containment is the point: an LLM choosing *what to try* is useful; an LLM
choosing *what is true* is not.

Strategy families:

``DEFER``
    Push the blocked work onto the next reserve day.  Downstream scenes travel
    with it — a scene whose prerequisite has moved past it has to move too, or
    the plan is dead on arrival at rule C008.

``SWAP``
    Exchange the blocked day with the nearest later shooting day.  A swap moves
    the entire day: location, call, wrap and scenes.  Only later days are
    considered; you cannot reschedule into the past.

``RELOCATE``
    Send the blocked scenes to an alternative location that supports their
    INT/EXT and time-of-day requirements.

``COMPRESS``
    Shed work until the day fits.  Used both as a first-class family and as the
    repair step when a plan busts the daily-hours cap or a permit wall.

Every function here is pure: no IO, no database, no clock reads beyond the
timestamps ``ProductionState.apply`` sets itself.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import yaml

from pri.domain.models import (
    CandidatePlan,
    ImpactReport,
    Move,
    MoveSceneToDay,
    ProductionState,
    RelocateScene,
    Scene,
    ShootingDay,
    SwapDays,
)
from pri.paths import config_path
from pri.persistence.serialization import content_digest

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from pri.domain.models import Location

__all__ = [
    "MAX_RAW_CANDIDATES",
    "STRATEGY_FAMILIES",
    "SWAP_SEARCH_WINDOW_DAYS",
    "CandidateGenerationError",
    "day_capacity_minutes",
    "day_load_minutes",
    "generate_candidates",
    "load_scoring_config",
]

STRATEGY_FAMILIES: tuple[str, ...] = ("DEFER", "SWAP", "RELOCATE", "COMPRESS")
MAX_RAW_CANDIDATES = 8
SWAP_SEARCH_WINDOW_DAYS = 3

_DEFAULT_SCORING_PATH = config_path("scoring.yaml")

#: Plan labels are assigned in generation order so the demo can say "Plan B"
#: and mean the same plan on every run.
_LABELS = "ABCDEFGH"


class CandidateGenerationError(ValueError):
    """Raised when a strategy family is asked for something it cannot express."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4)
def load_scoring_config(path: Path | None = None) -> dict[str, Any]:
    """Load ``config/scoring.yaml``.

    Cached: the generator and the scorer both need it, several times per
    recovery run, and re-reading a YAML file inside a scoring loop is a silly
    way to spend a demo's latency budget.

    Inputs:
        path: Override the config location.

    Outputs:
        The parsed configuration document.

    Failure modes:
        Raises ``FileNotFoundError`` if the file is absent.
        Raises ``yaml.YAMLError`` if it is malformed.
    """
    resolved = path if path is not None else _DEFAULT_SCORING_PATH
    with resolved.open(encoding="utf-8") as fh:
        loaded: dict[str, Any] = yaml.safe_load(fh)
    return loaded


# ---------------------------------------------------------------------------
# Schedule arithmetic shared with the repair loop
# ---------------------------------------------------------------------------


def day_load_minutes(
    day: ShootingDay,
    scenes_by_id: dict[str, Scene],
    setup_minutes_per_scene: int,
) -> int:
    """Total minutes of work a day is carrying, setups included.

    Inputs:
        day:                     The day to measure.
        scenes_by_id:            Lookup for the scenes on it.
        setup_minutes_per_scene: Turnaround charged per scene — unit move,
                                 relight, rehearsal.

    Outputs:
        Scene minutes plus one setup allowance per scheduled scene.
    """
    total = 0
    for sid in day.scene_ids:
        scene = scenes_by_id.get(sid)
        if scene is None:
            continue
        total += scene.estimated_minutes + setup_minutes_per_scene
    return total


def day_capacity_minutes(day: ShootingDay) -> int:
    """Minutes available between a day's call and its wrap."""
    return int((day.wrap_time - day.call_time).total_seconds() // 60)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_candidates(
    state: ProductionState,
    impact: ImpactReport,
    strategy_hints: Sequence[str] | None = None,
    *,
    scoring_config: dict[str, Any] | None = None,
) -> list[CandidatePlan]:
    """Expand the requested strategy families into concrete candidate plans.

    Inputs:
        state:          The state every plan is built against.
        impact:         The blast-radius report the plans are responding to.
        strategy_hints: Families to expand, in priority order.  Unknown names
                        are ignored; ``None`` or empty expands all four in the
                        canonical order.  This is the only input Gemini
                        influences.
        scoring_config: Override ``config/scoring.yaml`` (tests supply this).

    Outputs:
        Up to :data:`MAX_RAW_CANDIDATES` plans, labelled ``A``, ``B``, ``C`` …
        in generation order.  Plans whose moves leave the schedule unchanged, or
        that duplicate an earlier plan's resulting schedule, are dropped before
        labelling so the labels are dense.

    Failure modes:
        Does not raise for a family that has nothing to offer — it simply
        contributes no plans.  Raises :class:`CandidateGenerationError` only if
        the impact report names a day that is not in the schedule.
    """
    config = scoring_config if scoring_config is not None else load_scoring_config()
    setup_minutes = int(config.get("capacity", {}).get("setup_minutes_per_scene", 0))

    families = _resolve_families(strategy_hints)
    scenes_by_id = {s.id: s for s in state.scenes}
    days_by_date = {d.date: d for d in state.schedule.days}

    for affected in impact.affected_days:
        if affected not in days_by_date:
            raise CandidateGenerationError(f"Impact names {affected} but it is not a shooting day")

    builders = {
        "DEFER": _family_defer,
        "SWAP": _family_swap,
        "RELOCATE": _family_relocate,
        "COMPRESS": _family_compress,
    }

    proposals: list[tuple[str, tuple[Move, ...]]] = []
    for family in families:
        for moves in builders[family](state, impact, scenes_by_id, setup_minutes):
            if moves:
                proposals.append((family, moves))

    return _finalise(state, proposals)


def _resolve_families(strategy_hints: Sequence[str] | None) -> list[str]:
    """Filter and order the strategy families a set of hints asks for."""
    if not strategy_hints:
        return list(STRATEGY_FAMILIES)
    seen: list[str] = []
    for hint in strategy_hints:
        name = hint.strip().upper()
        if name in STRATEGY_FAMILIES and name not in seen:
            seen.append(name)
    return seen or list(STRATEGY_FAMILIES)


def _finalise(
    state: ProductionState,
    proposals: list[tuple[str, tuple[Move, ...]]],
) -> list[CandidatePlan]:
    """Drop no-ops and duplicates, then label the survivors A, B, C …"""
    base_digest = content_digest(state)
    seen_digests: set[str] = {base_digest}
    plans: list[CandidatePlan] = []

    for family, moves in proposals:
        if len(plans) >= MAX_RAW_CANDIDATES:
            break
        try:
            resulting = state.apply(list(moves))
        except (KeyError, ValueError):
            # A family proposed something the schedule cannot express; the
            # generator's job is to offer options, not to insist on them.
            continue
        digest = content_digest(resulting)
        if digest in seen_digests:
            continue
        seen_digests.add(digest)
        label = _LABELS[len(plans)]
        plans.append(
            CandidatePlan(
                id=f"plan-{label}",
                label=label,
                base_version=state.version,
                moves=moves,
                rationale_hint=family,
            )
        )
    return plans


# ---------------------------------------------------------------------------
# DEFER
# ---------------------------------------------------------------------------


def _family_defer(
    state: ProductionState,
    impact: ImpactReport,
    scenes_by_id: dict[str, Scene],
    setup_minutes: int,
) -> Iterable[tuple[Move, ...]]:
    """Push the blocked work onto the next reserve day, dragging only what it must.

    The directly-affected scenes move. A downstream scene moves *only if the
    deferral overtakes it* — if S28 already sits after the reserve day its
    prerequisite lands on, it stays put and the plan stays cheap. Moving it
    anyway would spend a second reserve day for nothing, and reserve days are
    the only slack the production has.
    """
    del setup_minutes  # capacity is not what constrains a reserve day here

    blocked_from = (
        min(impact.affected_days) if impact.affected_days else state.production.shoot_start
    )
    scheduled = _scene_slots(state)
    targets = _reserve_days_after(state, blocked_from)
    if not targets:
        return

    direct = [sid for sid in impact.directly_affected_scene_ids if sid in scheduled]
    if not direct:
        return

    placed = _place_deferred(direct, scenes_by_id, targets, {})
    if placed is None:
        return

    # Downstream scenes are considered in dependency order, so a scene that has
    # to move because its prerequisite moved is itself available as a
    # constraint on anything further down the chain.
    downstream = _dependency_order(
        [sid for sid in impact.downstream_scene_ids if sid in scheduled],
        scenes_by_id,
        scheduled,
    )
    for sid in downstream:
        scene = scenes_by_id.get(sid)
        prereq_dates = [
            placed.get(p, scheduled[p][0])
            for p in (scene.prerequisite_scene_ids if scene else ())
            if p in scheduled or p in placed
        ]
        current = scheduled[sid][0]
        if not prereq_dates or current > max(prereq_dates):
            continue  # the deferral did not overtake it; leave it alone
        slot = next((t for t in targets if t > max(prereq_dates)), None)
        if slot is None:
            return  # nowhere legal left to put it; DEFER has nothing to offer
        placed[sid] = slot

    moves = tuple(
        MoveSceneToDay(scene_id=sid, target_date=target)
        for sid, target in sorted(placed.items(), key=lambda kv: (kv[1], scheduled[kv[0]][1]))
        if scheduled[sid][0] != target
    )
    if moves:
        yield moves


def _place_deferred(
    ordered: Sequence[str],
    scenes_by_id: dict[str, Scene],
    targets: Sequence[date],
    placed: dict[str, date],
) -> dict[str, date] | None:
    """Place each scene on the earliest target day its prerequisites allow.

    Rule C008 wants a prerequisite on a *strictly earlier date*, so two scenes
    in the same dependency chain cannot share a reserve day.

    Inputs:
        ordered:      Scene ids, dependency order first.
        scenes_by_id: Scene lookup, for prerequisite edges.
        targets:      Candidate days, ascending.
        placed:       Assignments already made; not mutated.

    Outputs:
        A ``{scene_id: date}`` mapping including ``placed``, or ``None`` when
        the production has run out of later days to defer onto.
    """
    assignment = dict(placed)
    within = set(ordered)

    for sid in ordered:
        scene = scenes_by_id.get(sid)
        prereq_dates = [
            assignment[p]
            for p in (scene.prerequisite_scene_ids if scene else ())
            if p in within and p in assignment
        ]
        earliest = max(prereq_dates) if prereq_dates else None
        slot = next((t for t in targets if earliest is None or t > earliest), None)
        if slot is None:
            return None
        assignment[sid] = slot
    return assignment


def _reserve_days_after(state: ProductionState, after: date) -> list[date]:
    """Days a plan may put work on, strictly after ``after``.

    A day qualifies only if it is genuinely available: declared RESERVE on the
    board *and* listed in ``production.reserve_days``. An empty day is not the
    same as a free day — a travel day, a company move and a hold are all empty
    rows the unit cannot shoot on, and before ``day_kind`` existed the deferral
    family would happily schedule a scene onto one.
    """
    available = {day.date for day in state.schedule.days if day.is_available_for_scenes}
    return sorted(d for d in state.production.reserve_days if d > after and d in available)


def _scene_slots(state: ProductionState) -> dict[str, tuple[date, int]]:
    """Map every scheduled scene to its ``(date, position within day)`` slot."""
    slots: dict[str, tuple[date, int]] = {}
    for day in state.schedule.days:
        for index, sid in enumerate(day.scene_ids):
            slots[sid] = (day.date, index)
    return slots


def _dependency_order(
    scene_ids: Sequence[str],
    scenes_by_id: dict[str, Scene],
    slots: dict[str, tuple[date, int]],
) -> list[str]:
    """Order scenes so no scene precedes one of its prerequisites.

    Moves are applied in sequence and ``MoveSceneToDay`` appends, so emitting
    them in dependency order is what makes the destination day shootable.
    Ties are broken by the scenes' existing shooting order, which keeps the
    result stable across runs.

    Inputs:
        scene_ids:    The scenes to order.
        scenes_by_id: Scene lookup, for prerequisite edges.
        slots:        Current ``(date, index)`` positions, used as the tie-break.

    Outputs:
        The same scene ids, topologically sorted.

    Failure modes:
        A prerequisite cycle would be a corrupt fixture; the remaining scenes
        are appended in schedule order rather than raising, so one bad edge
        cannot take down the whole recovery run.
    """
    pending = sorted(scene_ids, key=lambda sid: slots.get(sid, (date.max, 0)))
    within = set(pending)
    ordered: list[str] = []
    placed: set[str] = set()

    while pending:
        progressed = False
        for sid in list(pending):
            scene = scenes_by_id.get(sid)
            prereqs = set(scene.prerequisite_scene_ids) & within if scene else set()
            if prereqs <= placed:
                ordered.append(sid)
                placed.add(sid)
                pending.remove(sid)
                progressed = True
        if not progressed:  # cycle — emit the rest in schedule order
            ordered.extend(pending)
            break
    return ordered


# ---------------------------------------------------------------------------
# SWAP
# ---------------------------------------------------------------------------


def _family_swap(
    state: ProductionState,
    impact: ImpactReport,
    scenes_by_id: dict[str, Scene],
    setup_minutes: int,
) -> Iterable[tuple[Move, ...]]:
    """Exchange the blocked day with the nearest later shooting day.

    Only later days are candidates.  A swap into the past would ask a unit to
    have shot something yesterday, and the impact window that blocked the day
    has usually already begun.
    """
    del scenes_by_id, setup_minutes

    if not impact.affected_days:
        return
    blocked = min(impact.affected_days)
    reserve = set(state.production.reserve_days)

    # SHOOT days only, on both sides. Swapping a shoot day with a travel day
    # would move the unit's work onto a coach.
    partners = sorted(
        day.date
        for day in state.schedule.days
        if blocked < day.date <= blocked + timedelta(days=SWAP_SEARCH_WINDOW_DAYS)
        and day.date not in reserve
        and day.is_shoot
        and day.scene_ids
    )
    for partner in partners[:1]:  # nearest compatible day only
        yield (SwapDays(date_a=blocked, date_b=partner),)


# ---------------------------------------------------------------------------
# RELOCATE
# ---------------------------------------------------------------------------


def _family_relocate(
    state: ProductionState,
    impact: ImpactReport,
    scenes_by_id: dict[str, Scene],
    setup_minutes: int,
) -> Iterable[tuple[Move, ...]]:
    """Send the blocked scenes somewhere that can actually host them.

    A location qualifies only if it supports every affected scene's INT/EXT and
    time-of-day.  Splitting the day across two locations is not offered: a unit
    move mid-day costs more than the plan saves.
    """
    del setup_minutes

    affected = [
        scenes_by_id[sid] for sid in impact.directly_affected_scene_ids if sid in scenes_by_id
    ]
    if not affected:
        return

    blocked_locations = set(impact.affected_location_ids)
    current = {scene.location_id for scene in affected}

    for location in state.locations:
        if location.id in blocked_locations or location.id in current:
            continue
        if not _location_supports_all(location, affected):
            continue
        yield tuple(
            RelocateScene(scene_id=scene.id, target_location_id=location.id) for scene in affected
        )


def _location_supports_all(location: Location, scenes: Sequence[Scene]) -> bool:
    """Whether one location can host every scene's INT/EXT and time of day."""
    return all(
        scene.int_ext in location.supports_int_ext
        and scene.time_of_day in location.supports_time_of_day
        for scene in scenes
    )


# ---------------------------------------------------------------------------
# COMPRESS
# ---------------------------------------------------------------------------


def _family_compress(
    state: ProductionState,
    impact: ImpactReport,
    scenes_by_id: dict[str, Scene],
    setup_minutes: int,
) -> Iterable[tuple[Move, ...]]:
    """Shed the shortest work until an over-committed day fits its window.

    Standalone this is a weak family — it does not solve a blocked location, it
    only makes an overloaded day legal. Its real value is as the repair step in
    :mod:`pri.engine.simulation.replan`, where it follows a call-time shift.
    """
    del impact

    for day in state.schedule.days:
        moves = compress_day(
            state,
            day.date,
            scenes_by_id,
            setup_minutes,
            new_call_time=day.call_time,
        )
        if moves:
            yield moves


def compress_day(
    state: ProductionState,
    day_date: date,
    scenes_by_id: dict[str, Scene],
    setup_minutes: int,
    *,
    new_call_time: datetime,
    maximum_daily_hours: float | None = None,
) -> tuple[Move, ...]:
    """Return the moves that shed whatever will not fit the day from ``new_call_time``.

    Two ceilings apply and the tighter one wins: the wall-clock window between
    the new call and the day's wrap, and the policy cap on daily hours. A repair
    that satisfies crew turnaround by starting later must not blow through the
    twelve-hour maximum to do it.

    Scenes are shed shortest-first: losing the 45-minute gate pickup costs the
    schedule least, and a producer reading the plan will recognise that as the
    call they would have made themselves. A scene another scene on the same day
    depends on is never shed, because that would strand the dependant.

    Inputs:
        state:               The state being replanned.
        day_date:            The day to bring within capacity.
        scenes_by_id:        Scene lookup.
        setup_minutes:       Per-scene turnaround allowance.
        new_call_time:       The call time to measure capacity against.
        maximum_daily_hours: Policy ceiling; read from ``capacity`` when omitted.

    Outputs:
        A tuple of :class:`MoveSceneToDay` moves, one per shed scene, in the
        order they are shed. Empty when the day already fits, or when there is
        no reserve day to shed onto.

    Failure modes:
        Does not raise for an unknown ``day_date``; returns no moves.
    """
    day = next((d for d in state.schedule.days if d.date == day_date), None)
    if day is None:
        return ()

    if maximum_daily_hours is None:
        config = load_scoring_config()
        maximum_daily_hours = float(config.get("capacity", {}).get("maximum_daily_hours", 12.0))

    window_minutes = int((day.wrap_time - new_call_time).total_seconds() // 60)
    capacity = min(window_minutes, int(maximum_daily_hours * 60))
    if capacity < 0:
        capacity = 0

    load = day_load_minutes(day, scenes_by_id, setup_minutes)
    if load <= capacity:
        return ()

    targets = _reserve_days_after(state, day_date)
    if not targets:
        return ()
    target = targets[0]

    shed_order = sorted(
        (sid for sid in day.scene_ids if sid in scenes_by_id),
        key=lambda sid: (scenes_by_id[sid].estimated_minutes, sid),
    )
    moves: list[Move] = []
    remaining = load
    on_day = set(day.scene_ids)

    for sid in shed_order:
        if remaining <= capacity:
            break
        depended_on = any(
            sid in scenes_by_id[other].prerequisite_scene_ids
            for other in on_day
            if other in scenes_by_id
        )
        if depended_on:
            continue
        moves.append(MoveSceneToDay(scene_id=sid, target_date=target))
        remaining -= scenes_by_id[sid].estimated_minutes + setup_minutes
        on_day.discard(sid)

    return tuple(moves)
