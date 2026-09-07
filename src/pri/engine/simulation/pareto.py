"""Pareto frontier over the valid candidate plans.

A producer does not want a single "best" recovery — the trade-off between
money, time and risk is theirs to make, and it depends on things the system
cannot see.  What the system *can* do is remove the options that are worse on
every axis at once, leaving only genuine choices on the table.

Minimised objectives, in this order:
    ``schedule_delay_days``, ``incremental_cost``, ``operational_risk``.

Only valid plans compete.  An invalid plan is not a cheap option, it is not an
option at all, and it is kept in the result set purely as evidence that the
validator did its job.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pri.domain.models import EvaluatedPlan, PlanScore

__all__ = ["dominates", "mark_pareto"]


def _objectives(score: PlanScore) -> tuple[Decimal, Decimal, Decimal]:
    """Return the three minimised objectives as exact Decimals.

    Delay and risk are floats on the model; converting via ``str`` keeps the
    comparison free of binary-float surprises where two plans are meant to tie.
    """
    return (
        Decimal(str(score.schedule_delay_days)),
        score.incremental_cost,
        Decimal(str(score.operational_risk)),
    )


def dominates(a: PlanScore, b: PlanScore) -> bool:
    """Whether ``a`` is at least as good as ``b`` everywhere and better somewhere.

    Inputs:
        a: Candidate score doing the dominating.
        b: Candidate score being tested.

    Outputs:
        ``True`` when ``a`` is no worse on delay, cost and risk, and strictly
        better on at least one of them.

    Failure modes:
        Does not raise.
    """
    left = _objectives(a)
    right = _objectives(b)
    return all(x <= y for x, y in zip(left, right, strict=True)) and left != right


def mark_pareto(evaluated: Sequence[EvaluatedPlan]) -> list[EvaluatedPlan]:
    """Flag the non-dominated valid plans.

    Inputs:
        evaluated: Every candidate from the recovery run, valid or not.

    Outputs:
        A new list in the original order.  Valid, scored plans carry an updated
        ``pareto_optimal``; invalid or unscored plans are returned with
        ``pareto_optimal=False``.  Input objects are never mutated.

    Failure modes:
        Does not raise.  A plan marked valid but carrying no score cannot
        compete and is treated as not on the frontier.
    """
    contenders = [ep for ep in evaluated if ep.valid and ep.score is not None]

    frontier: set[str] = set()
    for candidate in contenders:
        assert candidate.score is not None  # narrowed by the comprehension above
        beaten = any(
            other is not candidate
            and other.score is not None
            and dominates(other.score, candidate.score)
            for other in contenders
        )
        if not beaten:
            frontier.add(candidate.plan.id)

    return [ep.model_copy(update={"pareto_optimal": ep.plan.id in frontier}) for ep in evaluated]
