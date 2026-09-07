"""The second hardened disruption: the lead is out for a day.

The counterpart of `test_recovery_scenario.py`, and written to the same
standard, because the live-injection endpoint needs two disruption types a
judge can type with confidence rather than one. These assertions double as the
demo script for that half.

The scenario: Arun (P01) is unavailable on 2026-09-10, the day the board has
him on S17 and S18 at the ravine. He is also in S24 and S25 the following
night, which is what makes the naive fix wrong.

What this pins down, and why it is worth pinning:

    Before the disruption was projected into the state, the RELOCATE family
    produced a plan that moved S17 and S18 to another location and left them on
    the 10th — the day Arun is away — and PRI marked it valid. The SWAP family
    moved S24 and S25, both his, onto the 10th, and the only complaint was crew
    turnaround. Both would have been shown to a producer as options.

    `location.blocked` never had that failure, which is why it looked hardened
    when this one was not: moving a scene elsewhere genuinely resolves a
    blocked location. It does not resolve an absent actor.

The fix was not a new rule. C003 has always known how to check cast
availability; it had nothing to check against, because the event's claim was
never written into the state. See `engine/simulation/projection.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from pri.domain.models import DisruptionEvent, MoveSceneToDay
from pri.engine.constraints.validator import validate
from pri.engine.simulation.replan import recover

if TYPE_CHECKING:
    from pri.domain.models import EvaluatedPlan, ProductionState, RecoveryResult

IST = timezone(timedelta(hours=5, minutes=30))

SEP_10 = date(2026, 9, 10)
SEP_11 = date(2026, 9, 11)
SEP_16 = date(2026, 9, 16)

#: Arun. On S17 and S18 on the 10th, and on S24 and S25 on the 11th.
ARUN = "P01"


@pytest.fixture()
def arun_unavailable_event() -> DisruptionEvent:
    """Arun is out for the 10th. One day, the shape a first AD would report."""
    return DisruptionEvent(
        event_id="evt-arun-unavailable",
        production_id="film-001",
        event_type="actor.unavailable",
        occurred_at=datetime(2026, 9, 9, 18, 0, tzinfo=UTC),
        source="first_ad",
        severity=0.9,
        payload={
            "person_id": ARUN,
            "window_start": "2026-09-10T00:00:00+05:30",
            "window_end": "2026-09-11T00:00:00+05:30",
            "reason": "Sudden illness, doctor signed him off for the day",
        },
    )


@pytest.fixture()
def result(
    night_train_state: ProductionState, arun_unavailable_event: DisruptionEvent
) -> RecoveryResult:
    return recover(night_train_state, arun_unavailable_event, session_id="test-actor-session")


def _plan(result: RecoveryResult, label: str) -> EvaluatedPlan:
    return next(ep for ep in result.evaluated if ep.plan.label == label)


class TestTheImpact:
    def test_it_blocks_his_two_scenes_on_the_tenth(self, result: RecoveryResult) -> None:
        assert set(result.impact.directly_affected_scene_ids) == {"S17", "S18"}

    def test_it_names_him(self, result: RecoveryResult) -> None:
        assert ARUN in result.impact.affected_cast_ids

    def test_it_does_not_block_the_scene_he_is_not_in(self, result: RecoveryResult) -> None:
        """S21 is on the same day at the same location, and is Kiran's.

        An impact that blocked the whole day rather than the actor's own
        scenes would produce a much larger blast radius and a plan that moves
        work nobody needed to move.
        """
        assert "S21" not in result.impact.directly_affected_scene_ids


class TestTheRefusal:
    """The shot the demo exists for, in its cast-unavailability form."""

    def test_relocating_him_is_refused(self, result: RecoveryResult) -> None:
        """Plan C moves the scenes to another location and keeps the date.

        This is the plan that used to validate. Changing the address does not
        make an absent actor present, and PRI now says so with a rule code
        rather than offering it.
        """
        plan_c = _plan(result, "C")
        assert not plan_c.valid
        assert "C003" in {v.code for v in plan_c.violations}

    def test_the_refusal_names_him_and_the_scene(self, result: RecoveryResult) -> None:
        violation = next(v for v in _plan(result, "C").violations if v.code == "C003")
        assert violation.severity == "HARD"
        assert violation.subject_ids[0] == ARUN
        assert violation.subject_ids[1] in {"S17", "S18"}

    def test_the_refusal_is_readable_aloud(self, result: RecoveryResult) -> None:
        """The narration in the video reads this string off the screen."""
        violation = next(v for v in _plan(result, "C").violations if v.code == "C003")
        assert "Arun" in violation.message
        assert "unavailable" in violation.message

    def test_swapping_the_day_forward_is_also_refused(self, result: RecoveryResult) -> None:
        """Plan B swaps the 10th with the 11th, which is his as well.

        The trap in this scenario: the obvious swap moves S24 and S25 — both
        Arun's — onto the day he is away. A planner that only looked at the
        scenes named in the impact would take it.
        """
        plan_b = _plan(result, "B")
        assert not plan_b.valid
        assert "C003" in {v.code for v in plan_b.violations}


class TestTheRepair:
    def test_the_refused_relocate_is_repaired(self, result: RecoveryResult) -> None:
        plan_c2 = _plan(result, "C2")
        assert plan_c2.valid
        assert plan_c2.plan.rationale_hint == "repair of C for C003"

    def test_the_repair_moves_the_scenes_off_the_day_he_is_out(
        self, result: RecoveryResult
    ) -> None:
        moves = [m for m in _plan(result, "C2").plan.moves if isinstance(m, MoveSceneToDay)]
        assert {m.scene_id for m in moves} == {"S17", "S18"}
        assert {m.target_date for m in moves} == {SEP_16}

    def test_the_repair_lands_on_a_declared_reserve_day(
        self, result: RecoveryResult, night_train_state: ProductionState
    ) -> None:
        """Not merely an empty day. A hold or a company move is empty too."""
        assert SEP_16 in night_train_state.production.reserve_days
        day = next(d for d in night_train_state.schedule.days if d.date == SEP_16)
        assert day.is_available_for_scenes

    def test_the_repair_has_no_hard_violations(
        self, result: RecoveryResult, night_train_state: ProductionState
    ) -> None:
        resulting = night_train_state.apply(list(_plan(result, "C2").plan.moves))
        assert [v for v in validate(resulting) if v.severity == "HARD"] == []


class TestWhatTheProducerIsOffered:
    def test_at_least_one_plan_is_approvable(self, result: RecoveryResult) -> None:
        assert [ep for ep in result.evaluated if ep.valid]

    def test_nothing_invalid_reaches_the_frontier(self, result: RecoveryResult) -> None:
        for evaluated in result.evaluated:
            if evaluated.pareto_optimal:
                assert evaluated.valid, f"{evaluated.plan.id} is on the frontier while invalid"

    def test_the_frontier_is_the_defer_and_the_repair(self, result: RecoveryResult) -> None:
        frontier = sorted(ep.plan.label for ep in result.evaluated if ep.pareto_optimal)
        assert frontier == ["A", "C2"], frontier

    def test_deferring_is_offered_and_is_clean(self, result: RecoveryResult) -> None:
        """Plan A is the boring correct answer, and it must survive.

        A hardening change that only added refusals, leaving nothing valid,
        would pass a "no invalid plan is recommended" test while making the
        system useless.
        """
        plan_a = _plan(result, "A")
        assert plan_a.valid
        assert plan_a.pareto_optimal

    def test_every_candidate_is_scored_even_when_refused(self, result: RecoveryResult) -> None:
        for evaluated in result.evaluated:
            assert evaluated.score is not None


class TestItIsDeterministic:
    def test_two_runs_agree(
        self, night_train_state: ProductionState, arun_unavailable_event: DisruptionEvent
    ) -> None:
        """Same input, same plans, same verdicts, same numbers.

        Fingerprinted the way `test_recovery_scenario` does, and for the same
        reason: `ProductionState.apply` stamps `created_at` with the wall
        clock, so `resulting_state_digest` differs between two runs of
        identical input by construction. Comparing digests here would fail
        forever and say nothing about determinism.
        """

        def fingerprint(run: RecoveryResult) -> list[tuple[object, ...]]:
            return [
                (
                    ep.plan.id,
                    ep.plan.moves,
                    ep.valid,
                    tuple(sorted(v.code for v in ep.violations)),
                    ep.pareto_optimal,
                    ep.score.schedule_delay_days if ep.score else None,
                    str(ep.score.incremental_cost) if ep.score else None,
                    ep.score.operational_risk if ep.score else None,
                )
                for ep in run.evaluated
            ]

        first = recover(night_train_state, arun_unavailable_event, session_id="s1")
        second = recover(night_train_state, arun_unavailable_event, session_id="s2")
        assert fingerprint(first) == fingerprint(second)


class TestTheProjectionDoesNotLeak:
    """The window is a planning constraint, not a committed fact."""

    def test_the_base_state_is_not_mutated(
        self, night_train_state: ProductionState, arun_unavailable_event: DisruptionEvent
    ) -> None:
        recover(night_train_state, arun_unavailable_event, session_id="s3")
        arun = next(p for p in night_train_state.people if p.id == ARUN)
        assert arun.unavailable_windows == (), (
            "the recovery wrote the disruption into the caller's state; "
            "execution recomputes from the committed state and would disagree"
        )
