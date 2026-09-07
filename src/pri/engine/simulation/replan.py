"""The replanning loop — the moment the demo exists for.

Round one generates candidates and validates them.  Some fail.  Rather than
discarding a failure, the loop reads the rule that rejected it and builds a
repair aimed at that specific violation: a crew-turnaround breach becomes a
later call, and a day that no longer fits its window sheds its shortest scene.
Round two validates the repairs.

The failed candidate is kept in the result, deliberately.  A recovery system
that only ever shows its successes is indistinguishable from one that never
checks.  Plan B's rejection — *C001 crew_turnaround, observed 9.0h, required
>= 10.0h* — is the evidence that the numbers on screen were computed rather
than narrated.

Nothing here calls an LLM.  ``strategy_hints`` arrives as a list of strings and
is passed straight to the generator.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pri.domain.models import (
    CandidatePlan,
    ConstraintViolation,
    DisruptionEvent,
    EvaluatedPlan,
    Move,
    MoveSceneToDay,
    ProductionState,
    RecoveryResult,
    RoundTrace,
    Scene,
    ShiftCallTime,
)
from pri.engine.constraints.validator import Policy, load_policy, validate
from pri.engine.graph.dependency import impact_of
from pri.engine.simulation.candidates import (
    compress_day,
    generate_candidates,
    load_scoring_config,
)
from pri.engine.simulation.pareto import mark_pareto
from pri.engine.simulation.projection import project_disruption
from pri.engine.simulation.scoring import score
from pri.persistence.serialization import state_to_snapshot

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["ReplanError", "evaluate_plan", "recover"]


class ReplanError(ValueError):
    """Raised when the recovery loop is given inputs it cannot plan against."""


def recover(
    state: ProductionState,
    event: DisruptionEvent,
    strategy_hints: Sequence[str] | None = None,
    max_rounds: int = 2,
    *,
    session_id: str | None = None,
    policy: Policy | None = None,
    scoring_config: dict[str, Any] | None = None,
) -> RecoveryResult:
    """Generate, validate, repair and score recovery options for one disruption.

    Inputs:
        state:          The current committed state — the base every plan is
                        built on.
        event:          The disruption to recover from.
        strategy_hints: Which families to expand, in order.  Supplied by Gemini
                        in the agent path and by the caller in the deterministic
                        path; either way it only selects families.
        max_rounds:     ``1`` generates and validates without repairing.  ``2``
                        (the default) runs the repair round.  Higher values are
                        accepted but the loop stops once a round produces no
                        new repairs.
        session_id:     Override the generated session identifier.
        policy:         Override the constraint policy (tests).
        scoring_config: Override ``config/scoring.yaml`` (tests).

    Outputs:
        A :class:`RecoveryResult` holding every candidate — valid and invalid —
        with Pareto flags applied, plus one :class:`RoundTrace` per round.

    Failure modes:
        Raises :class:`ReplanError` if the event belongs to a different
        production than the state.
        Raises ``ValueError`` from :func:`impact_of` for an unrecognised
        event type.
    """
    if event.production_id != state.production.id:
        raise ReplanError(
            f"Event {event.event_id!r} belongs to production "
            f"{event.production_id!r}, not {state.production.id!r}"
        )

    resolved_policy = policy if policy is not None else load_policy()
    config = scoring_config if scoring_config is not None else load_scoring_config()
    scenes_by_id = {s.id: s for s in state.scenes}

    impact = impact_of(state, event)

    # Everything from here plans against the disruption as a constraint.
    #
    # An `actor.unavailable` event asserts that a person cannot work on given
    # dates, and until that is in the state, C003 has nothing to check: the
    # RELOCATE family produced plans that moved the blocked scenes to another
    # location and left them on the day the actor was away, and they validated.
    # See `simulation.projection`. The projection only ever adds a restriction,
    # so it can turn a valid plan invalid but never the reverse, and it is
    # never committed — execution recomputes from the real state.
    state = project_disruption(state, event)
    hints = list(strategy_hints) if strategy_hints else []

    evaluated: list[EvaluatedPlan] = []
    rounds: list[RoundTrace] = []

    # ---- Round 1: generate and validate ---------------------------------
    generated = generate_candidates(state, impact, hints, scoring_config=config)
    round_one = [
        evaluate_plan(state, plan, policy=resolved_policy, scoring_config=config)
        for plan in generated
    ]
    evaluated.extend(round_one)

    invalid = [ep for ep in round_one if not ep.valid]
    rounds.append(
        RoundTrace(
            round_number=1,
            strategy_hints=tuple(hints) if hints else ("DEFER", "SWAP", "RELOCATE", "COMPRESS"),
            generated_plan_ids=tuple(ep.plan.id for ep in round_one),
            invalid_plan_ids=tuple(ep.plan.id for ep in invalid),
            failed_rule_codes=_rule_codes(invalid),
            repaired_plan_ids=(),
            note=_round_one_note(round_one, invalid),
        )
    )

    # ---- Round 2: repair what failed ------------------------------------
    if max_rounds >= 2 and invalid:
        repairs: list[EvaluatedPlan] = []
        for failed in invalid:
            repair_plan = build_repair(state, failed, scenes_by_id, resolved_policy, config)
            if repair_plan is None:
                continue
            repairs.append(
                evaluate_plan(state, repair_plan, policy=resolved_policy, scoring_config=config)
            )
        evaluated.extend(repairs)
        rounds.append(
            RoundTrace(
                round_number=2,
                strategy_hints=("COMPRESS",),
                generated_plan_ids=tuple(ep.plan.id for ep in repairs),
                invalid_plan_ids=tuple(ep.plan.id for ep in repairs if not ep.valid),
                failed_rule_codes=_rule_codes([ep for ep in repairs if not ep.valid]),
                repaired_plan_ids=tuple(ep.plan.id for ep in repairs),
                note=_round_two_note(invalid, repairs),
            )
        )

    return RecoveryResult(
        session_id=session_id or f"rec-{uuid.uuid4().hex[:12]}",
        production_id=state.production.id,
        event_id=event.event_id,
        base_version=state.version,
        impact=impact,
        evaluated=tuple(mark_pareto(evaluated)),
        rounds=tuple(rounds),
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_plan(
    state: ProductionState,
    plan: CandidatePlan,
    *,
    policy: Policy | None = None,
    scoring_config: dict[str, Any] | None = None,
) -> EvaluatedPlan:
    """Apply, validate and score a single candidate.

    An invalid plan is still scored.  A producer comparing options needs to know
    what the rejected one *would* have cost — that is often why they ask for a
    repair rather than accepting the safe, expensive alternative.

    Inputs:
        state:          The base state.
        plan:           The candidate to evaluate.
        policy:         Constraint policy override.
        scoring_config: Scoring config override.

    Outputs:
        An :class:`EvaluatedPlan` with ``pareto_optimal`` left at ``False`` —
        the frontier is a property of the whole set, assigned later by
        :func:`~pri.engine.simulation.pareto.mark_pareto`.

    Failure modes:
        Raises ``KeyError`` or ``ValueError`` if the plan's moves cannot be
        applied to ``state`` — a generator bug, not a planning outcome.
    """
    resulting = state.apply(list(plan.moves))
    violations = validate(resulting, policy=policy)
    hard = [v for v in violations if v.severity == "HARD"]
    _, digest = state_to_snapshot(resulting)

    return EvaluatedPlan(
        plan=plan,
        valid=not hard,
        violations=tuple(violations),
        score=score(state, resulting, config=scoring_config),
        resulting_state_digest=digest,
        pareto_optimal=False,
    )


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------


def build_repair(
    state: ProductionState,
    failed: EvaluatedPlan,
    scenes_by_id: dict[str, Scene],
    policy: Policy,
    config: dict[str, Any],
) -> CandidatePlan | None:
    """Build a plan that keeps ``failed``'s intent but satisfies the rule it broke.

    Currently repairs C001 (crew turnaround): move the offending day's call to
    the earliest legal time plus a buffer, then shed scenes until the shortened
    day still fits its window.  Both halves are needed — a later call with the
    same workload just trades a turnaround violation for an overrun.

    The repair is labelled by appending ``2`` to the original label, so Plan B
    repairs to Plan B2 and the demo timeline reads as a sentence.

    Inputs:
        state:        The base state the original plan was built on.
        failed:       The evaluated plan that did not pass.
        scenes_by_id: Scene lookup.
        policy:       Active constraint policy, for the turnaround minimum.
        config:       Scoring config, for the buffer and setup allowance.

    Outputs:
        A new :class:`CandidatePlan`, or ``None`` when the failure is not one
        this function knows how to repair or the repair changes nothing.

    Failure modes:
        Does not raise; an unrepairable failure returns ``None`` and the
        original stays on screen as a rejected option.
    """
    turnaround = next(
        (v for v in failed.violations if v.code == "C001" and v.severity == "HARD"), None
    )
    unavailable = [v for v in failed.violations if v.code == "C003" and v.severity == "HARD"]

    intermediate = state.apply(list(failed.plan.moves))

    if turnaround is not None:
        repair_moves = _repair_turnaround(intermediate, turnaround, scenes_by_id, policy, config)
        repaired_code = turnaround.code
    elif unavailable:
        repair_moves = _repair_cast_availability(intermediate, unavailable)
        repaired_code = "C003"
    else:
        return None

    if not repair_moves:
        return None

    return CandidatePlan(
        id=f"{failed.plan.id}2",
        label=f"{failed.plan.label}2",
        base_version=state.version,
        moves=(*failed.plan.moves, *repair_moves),
        rationale_hint=f"repair of {failed.plan.label} for {repaired_code}",
    )


def _repair_cast_availability(
    intermediate: ProductionState,
    violations: list[ConstraintViolation],
) -> tuple[Move, ...]:
    """Move each scene the absent actor is still called for onto a free day.

    The counterpart of `_repair_turnaround`, for the other disruption type the
    live-injection endpoint accepts. A plan that leaves an actor's scene inside
    their unavailable window cannot be repaired by shifting a call time — the
    scene has to leave the day, and the only days it may go to are the reserve
    days the board actually declares.

    Inputs:
        intermediate: The state the failed plan produced.
        violations:   Its C003 violations. ``subject_ids`` is
                      ``(person_id, scene_id, date)``.

    Outputs:
        One :class:`MoveSceneToDay` per offending scene. Empty when there is no
        reserve day left to move to, which is a real answer: the schedule has
        run out of room and the producer is told so rather than shown a plan
        that pretends otherwise.
    """
    reserve = [
        day.date
        for day in sorted(intermediate.schedule.days, key=lambda d: d.date)
        if day.is_available_for_scenes and day.date in intermediate.production.reserve_days
    ]
    if not reserve:
        return ()

    blocked_by_person: dict[str, set[date]] = {}
    for person in intermediate.people:
        blocked_by_person[person.id] = {
            day.date
            for day in intermediate.schedule.days
            for window in person.unavailable_windows
            if day.call_time < window.end and window.start < day.wrap_time
        }

    scenes_by_id = {scene.id: scene for scene in intermediate.scenes}
    moves: list[Move] = []
    seen: set[str] = set()
    for violation in violations:
        if len(violation.subject_ids) < 2:
            continue
        scene_id = violation.subject_ids[1]
        if scene_id in seen:
            continue
        scene = scenes_by_id.get(scene_id)
        if scene is None:
            continue
        # A reserve day nobody in the scene is unavailable on. Checking every
        # cast member rather than only the one who triggered the violation
        # stops the repair trading one absence for another.
        target = next(
            (
                candidate
                for candidate in reserve
                if not any(candidate in blocked_by_person.get(pid, set()) for pid in scene.cast_ids)
            ),
            None,
        )
        if target is None:
            continue
        seen.add(scene_id)
        moves.append(MoveSceneToDay(scene_id=scene_id, target_date=target))

    return tuple(moves)


def _repair_turnaround(
    intermediate: ProductionState,
    violation: ConstraintViolation,
    scenes_by_id: dict[str, Scene],
    policy: Policy,
    config: dict[str, Any],
) -> tuple[Move, ...]:
    """Push the late day's call out to a legal turnaround, then refit the day.

    Both halves are needed. A later call on its own buys the crew their rest
    and hands the day an overrun instead; shedding scenes on its own leaves the
    turnaround short. The pair is the repair.

    Inputs:
        intermediate: The state the failed plan produced.
        violation:    The C001 violation, whose ``subject_ids`` name the day
                      pair as ISO dates.
        scenes_by_id: Scene lookup.
        policy:       Active policy, for the turnaround minimum.
        config:       Scoring config, for the buffer and setup allowance.

    Outputs:
        A :class:`ShiftCallTime` followed by any :class:`MoveSceneToDay` needed
        to fit the shortened day. Empty when the day cannot be repaired.

    Failure modes:
        Does not raise; an unrepairable violation returns no moves.
    """
    if len(violation.subject_ids) < 2:
        return ()
    early_date = _parse_date(violation.subject_ids[0])
    late_date = _parse_date(violation.subject_ids[1])
    if late_date is None or early_date is None:
        return ()

    days = {d.date: d for d in intermediate.schedule.days}
    early = days.get(early_date)
    late = days.get(late_date)
    if early is None or late is None:
        return ()

    minimum_hours = float(policy.get("crew_turnaround", {}).get("minimum_hours", 10.0))
    buffer_minutes = int(config.get("repair", {}).get("turnaround_buffer_minutes", 0))
    capacity_cfg = config.get("capacity", {})
    setup_minutes = int(capacity_cfg.get("setup_minutes_per_scene", 0))
    maximum_daily_hours = float(capacity_cfg.get("maximum_daily_hours", 12.0))

    new_call: datetime = (
        early.wrap_time + timedelta(hours=minimum_hours) + timedelta(minutes=buffer_minutes)
    )
    if new_call >= late.wrap_time:
        return ()  # the day would have no hours left; this plan cannot be repaired

    shed = compress_day(
        intermediate,
        late_date,
        scenes_by_id,
        setup_minutes,
        new_call_time=new_call,
        maximum_daily_hours=maximum_daily_hours,
    )
    return (ShiftCallTime(date=late_date, new_call_time=new_call), *shed)


def _parse_date(value: str) -> Any:
    """Parse an ISO date from a violation subject id, or ``None`` if it is not one."""
    from datetime import date as date_cls

    try:
        return date_cls.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Trace narration
# ---------------------------------------------------------------------------


def _rule_codes(plans: Sequence[EvaluatedPlan]) -> tuple[str, ...]:
    """Distinct HARD rule codes across a set of failed plans, sorted."""
    codes = {v.code for ep in plans for v in ep.violations if v.severity == "HARD"}
    return tuple(sorted(codes))


def _round_one_note(all_plans: Sequence[EvaluatedPlan], invalid: Sequence[EvaluatedPlan]) -> str:
    """One line describing round one, as the UI timeline renders it."""
    count = len(all_plans)
    if not count:
        return "No candidate plan could be generated for this disruption."
    if not invalid:
        return f"{count} candidates generated, all valid."
    failures = ", ".join(f"{ep.plan.label} INVALID - {_first_violation_text(ep)}" for ep in invalid)
    return f"{count} candidates generated. {failures}"


def _round_two_note(invalid: Sequence[EvaluatedPlan], repairs: Sequence[EvaluatedPlan]) -> str:
    """One line describing the repair round."""
    if not repairs:
        return "No repair could be generated for the rejected candidates."
    valid = [ep for ep in repairs if ep.valid]
    labels = ", ".join(ep.plan.label for ep in repairs)
    if len(valid) == len(repairs):
        return f"Replanned {len(invalid)} rejected candidate(s); {labels} generated and valid."
    return (
        f"Replanned {len(invalid)} rejected candidate(s); {labels} generated, some still invalid."
    )


def _first_violation_text(plan: EvaluatedPlan) -> str:
    """Render a plan's first HARD violation the way the narrator reads it aloud."""
    violation = next((v for v in plan.violations if v.severity == "HARD"), None)
    if violation is None:
        return "no violation recorded"
    return f"{violation.code}: observed {violation.observed}, required {violation.required}"
