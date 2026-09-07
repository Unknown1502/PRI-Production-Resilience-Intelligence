"""Deterministic narration of a recovery result.

Gemini writes better prose than this.  That is the only thing it does better,
and the hosted demo has to survive it being unavailable — a quota error at 2am
must degrade the explanation, not the pipeline.  So the same six numbers get a
templated voice here, and the UI shows a "deterministic mode" badge.

Everything below is assembled from values the engine computed.  There is no
model call and no invented figure: if a number appears in this text, it came
out of :mod:`pri.engine.simulation.scoring`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pri.domain.models import EvaluatedPlan, RecoveryResult

__all__ = ["explain", "recommend", "summarise_tradeoffs"]


def recommend(result: RecoveryResult) -> EvaluatedPlan | None:
    """Pick the plan to put in front of the producer.

    Among the Pareto-optimal options, prefer the one that protects the
    schedule: least delay first, then cost, then risk.  A recovery system whose
    default answer is "shoot it later" is not much of a recovery system.

    Inputs:
        result: A completed recovery run.

    Outputs:
        The recommended plan, or ``None`` when nothing valid was found.
    """
    frontier = [ep for ep in result.pareto_plans if ep.score is not None]
    if not frontier:
        frontier = [ep for ep in result.valid_plans if ep.score is not None]
    if not frontier:
        return None
    return min(
        frontier,
        key=lambda ep: (
            ep.score.schedule_delay_days,  # type: ignore[union-attr]
            ep.score.incremental_cost,  # type: ignore[union-attr]
            ep.score.operational_risk,  # type: ignore[union-attr]
        ),
    )


def explain(result: RecoveryResult, currency: str = "USD") -> str:
    """Write the producer-facing explanation of what happened and what to do.

    Inputs:
        result:   The completed recovery run.
        currency: ISO code used when quoting money.

    Outputs:
        A short multi-paragraph explanation.  Every figure in it is one the
        engine computed.

    Failure modes:
        Does not raise; a run with no candidates produces a plain statement to
        that effect rather than an empty string.
    """
    impact = result.impact
    paragraphs: list[str] = []

    direct = ", ".join(impact.directly_affected_scene_ids) or "no scenes"
    downstream = ", ".join(impact.downstream_scene_ids)
    blast = f"{impact.blast_radius * 100:.0f}%"
    what_broke = (
        f"The disruption blocks {direct} "
        f"({len(impact.affected_days)} shooting day"
        f"{'s' if len(impact.affected_days) != 1 else ''} affected), "
        f"a blast radius of {blast} of the remaining schedule."
    )
    if downstream:
        what_broke += f" {downstream} cannot shoot until that work is recovered."
    paragraphs.append(what_broke)

    rejected = [ep for ep in result.evaluated if not ep.valid]
    for plan in rejected:
        violation = next((v for v in plan.violations if v.severity == "HARD"), None)
        if violation is None:
            continue
        paragraphs.append(
            f"Plan {plan.plan.label} failed deterministic validation on rule "
            f"{violation.code}: observed {violation.observed}, "
            f"required {violation.required}. The planner generated a repair."
        )

    options = [ep for ep in result.pareto_plans if ep.score is not None]
    if options:
        paragraphs.append(
            "Two options survive the trade-off:"
            if len(options) == 2
            else f"{len(options)} option{'s' if len(options) != 1 else ''} survive the trade-off:"
        )
        paragraphs.extend(_option_line(ep, currency) for ep in options)

    chosen = recommend(result)
    if chosen is not None and chosen.score is not None:
        paragraphs.append(
            f"Recommended: Plan {chosen.plan.label}. It holds the schedule to "
            f"{chosen.score.schedule_delay_days:.2f} days of slip for "
            f"{currency} {chosen.score.incremental_cost:,.0f}, at an operational "
            f"risk of {chosen.score.operational_risk:.2f}."
        )
    elif not result.valid_plans:
        paragraphs.append(
            "No candidate satisfied the hard constraints. This needs a human "
            "decision about which rule to relax."
        )

    return "\n\n".join(paragraphs)


def summarise_tradeoffs(result: RecoveryResult, currency: str = "USD") -> str:
    """One line per option, for the comparison strip above the plan cards."""
    options = [ep for ep in result.evaluated if ep.score is not None]
    if not options:
        return "No scored candidates."
    return " | ".join(_compact(ep, currency) for ep in options)


def _compact(plan: EvaluatedPlan, currency: str) -> str:
    """``B2 valid - 1.52d, USD 19,655, risk 0.35`` for the comparison strip."""
    score = plan.score
    if score is None:  # pragma: no cover - filtered by the caller
        return f"{plan.plan.label} unscored"
    state = "valid" if plan.valid else "INVALID"
    return (
        f"{plan.plan.label} {state} - "
        f"{score.schedule_delay_days:.2f}d, "
        f"{currency} {score.incremental_cost:,.0f}, "
        f"risk {score.operational_risk:.2f}"
    )


def _option_line(plan: EvaluatedPlan, currency: str) -> str:
    """A sentence describing one Pareto-optimal option."""
    score = plan.score
    if score is None:  # pragma: no cover - filtered by the caller
        return f"Plan {plan.plan.label}: unscored."
    return (
        f"Plan {plan.plan.label} ({plan.plan.rationale_hint or 'plan'}): "
        f"{score.schedule_delay_days:.2f} days of slip, "
        f"{currency} {score.incremental_cost:,.0f} incremental cost, "
        f"operational risk {score.operational_risk:.2f}, "
        f"{score.affected_scene_count} scene"
        f"{'s' if score.affected_scene_count != 1 else ''} touched."
    )
