"""Deterministic plan scoring.

Produces the :class:`PlanScore` that a producer reads and that Gemini is allowed
to quote.  Every weight, rate and saturation point comes from
``config/scoring.yaml``; there are no numeric literals in the arithmetic below,
so the model can be audited by reading one YAML file rather than this module.

The six dimensions:

``schedule_delay_days``
    How far the worst-hit scene slipped, VFX plates weighted harder because
    their slip propagates to a vendor with a lock date.  A plan that moves no
    scene but reshapes a day is charged a part-day equivalent.

``incremental_cost``
    Location days newly charged, cast held on days they no longer shoot,
    equipment kept past its planned wrap, crew overtime, and reserve-day
    activation.  ``Decimal`` throughout — money never touches a float.

``operational_risk``
    A weighted, saturating blend of night moves, exterior weather exposure,
    reserve slack consumed and cast holding, clamped to ``[0, 1]``.

``affected_scene_count``
    Scenes whose date or location changed.

``crew_disruption_hours``
    Hours of call-time movement and day-length change the unit absorbs.

``downstream_dependency_impact``
    Moved scenes that other scenes depend on.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pri.domain.models import PlanScore, ProductionState, Scene

if TYPE_CHECKING:
    from datetime import date

__all__ = ["ScoringError", "score"]


class ScoringError(ValueError):
    """Raised when the scoring configuration is missing a required weight."""


# ---------------------------------------------------------------------------
# Shape extraction — the two states reduced to comparable facts
# ---------------------------------------------------------------------------


class _Shape:
    """The facts about a state that scoring compares.

    Extracted once per state so the six dimensions do not each re-walk the
    schedule.
    """

    __slots__ = (
        "call_times",
        "cast_days",
        "day_durations",
        "equipment_days",
        "location_days",
        "scene_dates",
        "scene_locations",
        "shooting_dates",
    )

    def __init__(self, state: ProductionState) -> None:
        scenes_by_id = {s.id: s for s in state.scenes}

        self.scene_dates: dict[str, date] = {}
        self.scene_locations: dict[str, str] = {s.id: s.location_id for s in state.scenes}
        self.location_days: set[tuple[date, str]] = set()
        self.cast_days: set[tuple[str, date]] = set()
        self.equipment_days: set[tuple[str, date]] = set()
        self.call_times: dict[date, Any] = {}
        self.day_durations: dict[date, float] = {}
        self.shooting_dates: set[date] = set()

        for day in state.schedule.days:
            self.call_times[day.date] = day.call_time
            self.day_durations[day.date] = (day.wrap_time - day.call_time).total_seconds() / 3600.0
            if day.scene_ids:
                self.shooting_dates.add(day.date)
                self.location_days.add((day.date, day.location_id))
            for sid in day.scene_ids:
                self.scene_dates[sid] = day.date
                scene = scenes_by_id.get(sid)
                if scene is None:
                    continue
                for pid in scene.cast_ids:
                    self.cast_days.add((pid, day.date))
                for eid in scene.equipment_ids:
                    self.equipment_days.add((eid, day.date))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def score(
    base_state: ProductionState,
    candidate_state: ProductionState,
    *,
    config: dict[str, Any] | None = None,
) -> PlanScore:
    """Compare a candidate schedule to its base and price the difference.

    Inputs:
        base_state:      The state the plan was built on.
        candidate_state: The state that results from applying the plan.
        config:          Override ``config/scoring.yaml``.

    Outputs:
        A fully populated :class:`PlanScore`.

    Failure modes:
        Raises :class:`ScoringError` if the risk weights are absent or do not
        sum to 1.0 — a silently mis-weighted risk score is worse than none.
    """
    from pri.engine.simulation.candidates import load_scoring_config

    cfg = config if config is not None else load_scoring_config()
    base = _Shape(base_state)
    cand = _Shape(candidate_state)

    scenes_by_id = {s.id: s for s in base_state.scenes}
    moved = _moved_scenes(base, cand)
    held_cast_days = _held_cast_days(base, cand)
    relocated_plates = _relocated_plate_scenes(base, cand, scenes_by_id)

    return PlanScore(
        schedule_delay_days=_delay(base, cand, moved, relocated_plates, scenes_by_id, cfg),
        incremental_cost=_cost(base_state, base, cand, held_cast_days, relocated_plates, cfg),
        operational_risk=_risk(
            base, cand, moved, scenes_by_id, held_cast_days, relocated_plates, cfg
        ),
        affected_scene_count=_affected_scene_count(base, cand),
        crew_disruption_hours=_crew_disruption_hours(base, cand),
        downstream_dependency_impact=_downstream_impact(moved, base_state.scenes),
    )


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------


def _moved_scenes(base: _Shape, cand: _Shape) -> dict[str, tuple[date, date]]:
    """Scenes whose shooting date changed, as ``{scene_id: (from, to)}``."""
    moved: dict[str, tuple[date, date]] = {}
    for sid, old in base.scene_dates.items():
        new = cand.scene_dates.get(sid)
        if new is not None and new != old:
            moved[sid] = (old, new)
    return moved


def _relocated_plate_scenes(
    base: _Shape,
    cand: _Shape,
    scenes_by_id: dict[str, Scene],
) -> list[str]:
    """VFX-plate scenes that ended up at a different location than planned."""
    return [
        sid
        for sid, old_loc in base.scene_locations.items()
        if cand.scene_locations.get(sid) not in (None, old_loc)
        and (scene := scenes_by_id.get(sid)) is not None
        and scene.vfx_plate
    ]


def _delay(
    base: _Shape,
    cand: _Shape,
    moved: dict[str, tuple[date, date]],
    relocated_plates: list[str],
    scenes_by_id: dict[str, Scene],
    cfg: dict[str, Any],
) -> float:
    """Weighted slip of the affected work, in days.

    Weighting by scene duration is what separates "we pushed the 45-minute gate
    pickup into the reserve day" from "we pushed three and a half hours of
    plates into it".  Both move a scene six days; only one of them is a
    schedule problem.
    """
    delay_cfg = cfg.get("delay", {})
    vfx_multiplier = float(delay_cfg.get("vfx_plate_slip_multiplier", 1.0))
    measure = str(delay_cfg.get("measure", "duration_weighted_mean_slip"))
    plate_relocation_slip = float(delay_cfg.get("vfx_plate_relocation_slip_days", 0.0))

    slips: list[float] = []
    weights: list[float] = []
    for sid, (old, new) in moved.items():
        days = (new - old).days
        if days <= 0:
            continue  # pulling work earlier is not a delay
        scene = scenes_by_id.get(sid)
        multiplier = vfx_multiplier if scene is not None and scene.vfx_plate else 1.0
        slips.append(days * multiplier)
        weights.append(float(scene.estimated_minutes) if scene is not None else 1.0)

    # A relocated plate lands late even though its scene shoots on time.
    for sid in relocated_plates:
        scene = scenes_by_id.get(sid)
        slips.append(plate_relocation_slip)
        weights.append(float(scene.estimated_minutes) if scene is not None else 1.0)

    if slips:
        if measure == "max_scene_slip":
            return round(max(slips), 4)
        if measure == "mean_scene_slip":
            return round(sum(slips) / len(slips), 4)
        total_weight = sum(weights)
        if total_weight <= 0:
            return round(sum(slips) / len(slips), 4)
        return round(sum(s * w for s, w in zip(slips, weights, strict=True)) / total_weight, 4)

    # Nothing slipped. A reshaped day still costs part of a day.
    equivalent = float(delay_cfg.get("call_time_shift_day_equivalent", 0.0))
    reshaped = any(
        cand.call_times.get(d) != call or cand.day_durations.get(d) != base.day_durations.get(d)
        for d, call in base.call_times.items()
    )
    return equivalent if reshaped else 0.0


def _held_cast_days(base: _Shape, cand: _Shape) -> set[tuple[str, date]]:
    """Cast paid to stand by on a day that lost all of its work.

    A day that keeps shooting can release an actor who is no longer needed —
    that is an ordinary schedule change, not a cost.  A day that loses
    everything is different: the call is already committed and the unit is
    already travelling, so the cast are held and paid.
    """
    struck = base.shooting_dates - cand.shooting_dates
    return {(pid, day) for (pid, day) in base.cast_days if day in struck}


def _cost(
    base_state: ProductionState,
    base: _Shape,
    cand: _Shape,
    held_cast_days: set[tuple[str, date]],
    relocated_plates: list[str],
    cfg: dict[str, Any],
) -> Decimal:
    """Incremental cost of the candidate against its base, in production currency."""
    cost_cfg = cfg.get("cost", {})
    holding_fraction = Decimal(str(cost_cfg.get("cast_holding_rate_fraction", 0)))
    forfeit = bool(cost_cfg.get("forfeit_released_location", True))
    overtime_rate = Decimal(str(cost_cfg.get("overtime_rate_per_hour", 0)))
    reserve_fee = Decimal(str(cost_cfg.get("reserve_day_activation_fee", 0)))

    location_rates = {loc.id: loc.day_rate for loc in base_state.locations}
    person_rates = {p.id: p.daily_rate for p in base_state.people}
    equipment_rates = {e.id: e.daily_rate for e in base_state.equipment}

    total = Decimal(0)

    # Location days newly charged, and released days credited back unless the
    # booking is forfeit.
    for _day_date, loc_id in cand.location_days - base.location_days:
        total += location_rates.get(loc_id, Decimal(0))
    if not forfeit:
        for _day_date, loc_id in base.location_days - cand.location_days:
            total -= location_rates.get(loc_id, Decimal(0))

    # Cast held on days they no longer shoot.
    for pid, _day in held_cast_days:
        total += person_rates.get(pid, Decimal(0)) * holding_fraction

    # Equipment kept for days it was not originally booked for.
    for eid, _day in cand.equipment_days - base.equipment_days:
        total += equipment_rates.get(eid, Decimal(0))

    # Crew overtime: hours a day grew beyond its baseline length.
    overtime_hours = Decimal(0)
    for day_date, duration in cand.day_durations.items():
        baseline = base.day_durations.get(day_date)
        if baseline is not None and duration > baseline:
            overtime_hours += Decimal(str(round(duration - baseline, 4)))
    total += overtime_hours * overtime_rate

    # Reserve days brought into play.
    activated = {
        d
        for d in cand.shooting_dates - base.shooting_dates
        if d in base_state.production.reserve_days
    }
    total += reserve_fee * len(activated)

    # VFX plates shot against a different background have to be reworked.
    plate_penalty = Decimal(str(cost_cfg.get("vfx_plate_relocation_penalty", 0)))
    total += plate_penalty * len(relocated_plates)

    return total


def _risk(
    base: _Shape,
    cand: _Shape,
    moved: dict[str, tuple[date, date]],
    scenes_by_id: dict[str, Scene],
    held_cast_days: set[tuple[str, date]],
    relocated_plates: list[str],
    cfg: dict[str, Any],
) -> float:
    """Weighted, saturating operational risk in ``[0, 1]``."""
    risk_cfg = cfg.get("risk", {})
    weights: dict[str, float] = {k: float(v) for k, v in risk_cfg.get("weights", {}).items()}
    saturation: dict[str, float] = {k: float(v) for k, v in risk_cfg.get("saturation", {}).items()}
    if not weights:
        raise ScoringError("config/scoring.yaml defines no risk.weights")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ScoringError(f"risk.weights must sum to 1.0, got {sum(weights.values()):.4f}")

    night_moves = sum(
        1
        for sid in moved
        if (scene := scenes_by_id.get(sid)) is not None and scene.time_of_day in {"NIGHT", "DUSK"}
    )
    ext_exposure = sum(
        1
        for sid in moved
        if (scene := scenes_by_id.get(sid)) is not None and scene.int_ext == "EXT"
    )
    reserve_consumed = len(cand.shooting_dates - base.shooting_dates)

    factors = {
        "night_moves": float(night_moves),
        "ext_weather_exposure": float(ext_exposure),
        "reserve_days_consumed": float(reserve_consumed),
        "cast_holding": float(len(held_cast_days)),
        "vfx_plate_relocation": float(len(relocated_plates)),
    }

    total = 0.0
    for name, weight in weights.items():
        ceiling = saturation.get(name, 1.0) or 1.0
        total += weight * min(factors.get(name, 0.0) / ceiling, 1.0)
    return round(min(max(total, 0.0), 1.0), 4)


def _affected_scene_count(base: _Shape, cand: _Shape) -> int:
    """Scenes whose shooting date or location changed."""
    affected = {
        sid for sid, old in base.scene_dates.items() if cand.scene_dates.get(sid) not in (None, old)
    }
    affected |= {
        sid
        for sid, old_loc in base.scene_locations.items()
        if cand.scene_locations.get(sid) not in (None, old_loc)
    }
    return len(affected)


def _crew_disruption_hours(base: _Shape, cand: _Shape) -> float:
    """Hours of call movement and day-length change the unit absorbs."""
    total = 0.0
    for day_date, base_call in base.call_times.items():
        cand_call = cand.call_times.get(day_date)
        if cand_call is not None:
            total += abs((cand_call - base_call).total_seconds()) / 3600.0
        base_duration = base.day_durations.get(day_date, 0.0)
        cand_duration = cand.day_durations.get(day_date, base_duration)
        total += abs(cand_duration - base_duration)
    return round(total, 4)


def _downstream_impact(
    moved: dict[str, tuple[date, date]],
    scenes: tuple[Scene, ...],
) -> int:
    """How many moved scenes something else depends on."""
    dependants: dict[str, int] = defaultdict(int)
    for scene in scenes:
        for prereq in scene.prerequisite_scene_ids:
            dependants[prereq] += 1
    return sum(1 for sid in moved if dependants.get(sid, 0) > 0)
