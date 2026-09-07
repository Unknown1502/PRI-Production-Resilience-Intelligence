"""The recovery scenario, end to end, against the real fixture.

PROMPT 07 names six of these tests non-negotiable. They are both tests and demo
evidence: if one goes red, the video is narrating something the system no longer
does.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from pri.domain.models import (
    DisruptionEvent,
    EvaluatedPlan,
    MoveSceneToDay,
    ProductionState,
    RecoveryResult,
    ShiftCallTime,
    SwapDays,
)
from pri.engine.constraints.validator import validate
from pri.engine.simulation.replan import ReplanError, recover

IST = timezone(timedelta(hours=5, minutes=30))

SEP_10 = date(2026, 9, 10)
SEP_11 = date(2026, 9, 11)
SEP_16 = date(2026, 9, 16)
SEP_17 = date(2026, 9, 17)


@pytest.fixture()
def result(
    night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
) -> RecoveryResult:
    return recover(night_train_state, loc04_blocked_event, session_id="test-session")


def _plan(result: RecoveryResult, label: str) -> EvaluatedPlan:
    return next(ep for ep in result.evaluated if ep.plan.label == label)


class TestRecoveryScenario:
    """The six checks PROMPT 07 requires, plus the shape of the run around them."""

    def test_plan_b_invalid_with_c001(self, result: RecoveryResult) -> None:
        plan_b = _plan(result, "B")
        assert plan_b.valid is False
        hard = [v for v in plan_b.violations if v.severity == "HARD"]
        assert len(hard) == 1
        assert hard[0].code == "C001"

    def test_plan_b_observed_is_nine_hours(self, result: RecoveryResult) -> None:
        """The exact string the narrator reads aloud."""
        violation = next(v for v in _plan(result, "B").violations if v.code == "C001")
        assert violation.observed == "9.0h"
        assert violation.required == ">= 10.0h"

    def test_plan_b2_is_valid(self, result: RecoveryResult) -> None:
        assert _plan(result, "B2").valid is True

    def test_pareto_set_is_exactly_a_and_b2(self, result: RecoveryResult) -> None:
        assert [ep.plan.label for ep in result.pareto_plans] == ["A", "B2"]

    def test_result_is_deterministic_on_repeated_runs(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        def fingerprint(run: RecoveryResult) -> list[tuple[object, ...]]:
            return [
                (
                    ep.plan.id,
                    ep.plan.moves,
                    ep.valid,
                    ep.pareto_optimal,
                    ep.score.schedule_delay_days if ep.score else None,
                    str(ep.score.incremental_cost) if ep.score else None,
                    ep.score.operational_risk if ep.score else None,
                )
                for ep in run.evaluated
            ]

        first = recover(night_train_state, loc04_blocked_event)
        second = recover(night_train_state, loc04_blocked_event)
        assert fingerprint(first) == fingerprint(second)

    def test_round_trace_records_c001(self, result: RecoveryResult) -> None:
        first_round = result.rounds[0]
        assert first_round.invalid_plan_ids == ("plan-B",)
        assert first_round.failed_rule_codes == ("C001",)
        assert "C001" in first_round.note
        assert "observed 9.0h" in first_round.note
        assert "required >= 10.0h" in first_round.note


class TestGeneratedCandidates:
    """What the three families produce against the demo disruption."""

    def test_three_candidates_then_one_repair(self, result: RecoveryResult) -> None:
        assert [ep.plan.label for ep in result.evaluated] == ["A", "B", "C", "B2"]
        assert result.rounds[0].generated_plan_ids == ("plan-A", "plan-B", "plan-C")

    def test_families_are_defer_swap_relocate(self, result: RecoveryResult) -> None:
        assert _plan(result, "A").plan.rationale_hint == "DEFER"
        assert _plan(result, "B").plan.rationale_hint == "SWAP"
        assert _plan(result, "C").plan.rationale_hint == "RELOCATE"

    def test_impact_matches_the_fixture(self, result: RecoveryResult) -> None:
        impact = result.impact
        assert set(impact.directly_affected_scene_ids) == {"S17", "S18", "S21"}
        assert set(impact.downstream_scene_ids) == {"S28"}
        assert set(impact.affected_cast_ids) == {"P01", "P02", "P03", "P04"}
        assert SEP_10 in impact.affected_days

    def test_defer_spends_one_reserve_day(self, result: RecoveryResult) -> None:
        """S28 already sits after the reserve day, so the deferral leaves it alone."""
        moves = _plan(result, "A").plan.moves
        assert {(m.scene_id, m.target_date) for m in moves if isinstance(m, MoveSceneToDay)} == {
            ("S17", SEP_16),
            ("S18", SEP_16),
            ("S21", SEP_16),
        }

    def test_defer_keeps_the_downstream_scene_after_its_prerequisite(
        self, night_train_state: ProductionState, result: RecoveryResult
    ) -> None:
        applied = night_train_state.apply(list(_plan(result, "A").plan.moves))
        slots = {sid: day.date for day in applied.schedule.days for sid in day.scene_ids}
        assert slots["S18"] == SEP_16
        assert slots["S28"] == SEP_17
        assert slots["S18"] < slots["S28"]

    def test_swap_is_the_nearest_later_day(self, result: RecoveryResult) -> None:
        assert _plan(result, "B").plan.moves == (SwapDays(date_a=SEP_10, date_b=SEP_11),)

    def test_every_candidate_is_scored_even_when_invalid(self, result: RecoveryResult) -> None:
        assert all(ep.score is not None for ep in result.evaluated)


class TestRepair:
    """The B-to-B2 repair, derived from the rule rather than hard-coded."""

    def test_repair_shifts_the_call_to_0830(self, result: RecoveryResult) -> None:
        moves = _plan(result, "B2").plan.moves
        assert moves[0] == SwapDays(date_a=SEP_10, date_b=SEP_11)
        assert moves[1] == ShiftCallTime(
            date=SEP_11, new_call_time=datetime.combine(SEP_11, time(8, 30), tzinfo=IST)
        )

    def test_repair_restores_a_ten_and_a_half_hour_turnaround(
        self, night_train_state: ProductionState, result: RecoveryResult
    ) -> None:
        repaired = night_train_state.apply(list(_plan(result, "B2").plan.moves))
        days = {d.date: d for d in repaired.schedule.days}
        assert days[SEP_11].call_time - days[SEP_10].wrap_time == timedelta(hours=10, minutes=30)

    def test_repair_keeps_the_courtyard_inside_its_permit(
        self, night_train_state: ProductionState, result: RecoveryResult
    ) -> None:
        """08:30 to 19:00 — LOC-04's permit wall is what pins the wrap."""
        repaired = night_train_state.apply(list(_plan(result, "B2").plan.moves))
        sep_11 = next(d for d in repaired.schedule.days if d.date == SEP_11)
        assert sep_11.location_id == "LOC-04"
        assert sep_11.wrap_time == datetime.combine(SEP_11, time(19, 0), tzinfo=IST)
        assert sep_11.wrap_time - sep_11.call_time == timedelta(hours=10, minutes=30)

    def test_repair_has_no_hard_violations(
        self, night_train_state: ProductionState, result: RecoveryResult
    ) -> None:
        repaired = night_train_state.apply(list(_plan(result, "B2").plan.moves))
        assert [v.code for v in validate(repaired) if v.severity == "HARD"] == []

    def test_round_two_reports_the_replan(self, result: RecoveryResult) -> None:
        assert result.rounds[1].round_number == 2
        assert result.rounds[1].repaired_plan_ids == ("plan-B2",)
        assert "Replanned" in result.rounds[1].note


class TestFrontier:
    """What survives the trade-off, and what does not."""

    def test_relocation_is_dominated_not_rejected(self, result: RecoveryResult) -> None:
        """C is a legal plan that simply is not worth it: worse on all three axes."""
        plan_c = _plan(result, "C")
        plan_b2 = _plan(result, "B2")
        assert plan_c.valid is True
        assert plan_c.pareto_optimal is False
        assert plan_c.score is not None
        assert plan_b2.score is not None
        assert plan_b2.score.schedule_delay_days <= plan_c.score.schedule_delay_days
        assert plan_b2.score.incremental_cost <= plan_c.score.incremental_cost
        assert plan_b2.score.operational_risk <= plan_c.score.operational_risk

    def test_defer_is_the_cheapest_option(self, result: RecoveryResult) -> None:
        scored = [ep for ep in result.evaluated if ep.valid and ep.score is not None]
        cheapest = min(scored, key=lambda ep: ep.score.incremental_cost)  # type: ignore[union-attr]
        assert cheapest.plan.label == "A"

    def test_invalid_plans_are_never_on_the_frontier(self, result: RecoveryResult) -> None:
        assert all(ep.pareto_optimal is False for ep in result.evaluated if not ep.valid)


class TestStrategyHints:
    """Gemini picks which families to explore. It cannot invent a plan."""

    def test_hints_filter_the_families(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        only_defer = recover(night_train_state, loc04_blocked_event, ["DEFER"])
        assert [ep.plan.rationale_hint for ep in only_defer.evaluated] == ["DEFER"]

    def test_hints_set_the_expansion_order(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        reordered = recover(night_train_state, loc04_blocked_event, ["RELOCATE", "DEFER"])
        assert [ep.plan.rationale_hint for ep in reordered.evaluated] == [
            "RELOCATE",
            "DEFER",
        ]

    def test_unknown_hints_fall_back_to_all_families(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        nonsense = recover(night_train_state, loc04_blocked_event, ["TELEPORT_THE_UNIT"])
        assert [ep.plan.label for ep in nonsense.evaluated] == ["A", "B", "C", "B2"]

    def test_event_for_another_production_is_refused(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        foreign = loc04_blocked_event.model_copy(update={"production_id": "film-999"})
        with pytest.raises(ReplanError, match="film-999"):
            recover(night_train_state, foreign)
