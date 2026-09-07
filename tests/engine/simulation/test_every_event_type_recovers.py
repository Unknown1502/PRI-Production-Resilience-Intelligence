"""Every disruption type PRI accepts, driven end to end.

The README said the honest thing: all five event types compute impact, but the
candidate families were designed against `location.blocked`, and whether an
`equipment.failed` event produces plans a producer would want was untested.
"Untested" is the part that was fixable, so these test it.

Each case runs the real pipeline on the Night Train fixture — impact, plan
generation, validation, repair, scoring — and asserts the three things that
make a recovery useful rather than merely non-crashing:

  * the event blocks something, so the scenario is real;
  * at least one valid plan comes back, so there is something to approve;
  * every plan that survives is genuinely valid, because a plan presented to a
    producer with a live violation is worse than no plan.

Two behaviours here are correct and might read as gaps. A recovery can return
an empty frontier if nothing it generates validates — that is a refusal, and it
is reported rather than papered over. And `weather.changed` blocks exterior
work regardless of severity, so its impact set is the widest of the five.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from pri.domain.models import DisruptionEvent
from pri.engine.simulation.replan import recover

if TYPE_CHECKING:
    from pri.domain.models import ProductionState

#: The window the demo turns on: the night the schedule has to move.
_WINDOW = {
    "window_start": "2026-09-10T00:00:00+05:30",
    "window_end": "2026-09-11T00:00:00+05:30",
}


def _event(event_type: str, payload: dict[str, Any]) -> DisruptionEvent:
    return DisruptionEvent(
        event_id=f"test-{event_type}",
        production_id="film-001",
        event_type=event_type,  # type: ignore[arg-type]
        occurred_at=datetime(2026, 9, 9, 18, 0, tzinfo=UTC),
        source="test",
        severity=0.8,
        payload=payload,
    )


#: One realistic disruption per accepted type, each aimed at the fixture's own
#: identifiers rather than invented ones — an event naming a location that does
#: not exist blocks nothing and would make this suite pass while proving
#: nothing.
CASES: list[tuple[str, dict[str, Any]]] = [
    ("location.blocked", {"location_id": "LOC-04", **_WINDOW, "reason": "Permit withdrawn"}),
    ("actor.unavailable", {"person_id": "P01", **_WINDOW, "reason": "Illness"}),
    ("equipment.failed", {"equipment_id": "CRANE-01", "reason": "Technocrane hydraulics failed"}),
    ("crew.unavailable", {"unit": "MAIN", **_WINDOW, "reason": "Grip crew stood down"}),
    ("weather.changed", {**_WINDOW, "condition": "storm", "reason": "Cyclone warning"}),
]


@pytest.mark.parametrize(("event_type", "payload"), CASES, ids=[c[0] for c in CASES])
class TestEveryAcceptedEventTypeRecovers:
    def test_it_blocks_something(
        self, night_train_state: ProductionState, event_type: str, payload: dict[str, Any]
    ) -> None:
        """A scenario that blocks nothing tests nothing."""
        result = recover(night_train_state, _event(event_type, payload))
        assert result.impact.directly_affected_scene_ids, (
            f"{event_type} blocked no scenes; the payload does not match the fixture"
        )

    def test_it_produces_a_plan_a_producer_could_approve(
        self, night_train_state: ProductionState, event_type: str, payload: dict[str, Any]
    ) -> None:
        result = recover(night_train_state, _event(event_type, payload))
        valid = [c for c in result.evaluated if c.valid]
        assert valid, (
            f"{event_type} produced {len(result.evaluated)} candidates and none "
            "validated — the strategy families do not cover this disruption"
        )

    def test_nothing_invalid_reaches_the_frontier(
        self, night_train_state: ProductionState, event_type: str, payload: dict[str, Any]
    ) -> None:
        """The frontier is what the UI recommends from.

        A candidate can be invalid and still be worth showing — the C001
        refusal in the demo is exactly that, and it is shown with its stamp.
        What it must never do is arrive on the Pareto frontier, which is the
        set the producer is being told to choose between.
        """
        result = recover(night_train_state, _event(event_type, payload))
        for candidate in result.evaluated:
            if candidate.pareto_optimal:
                assert candidate.valid, (
                    f"{event_type}: {candidate.plan.id} is on the frontier with "
                    f"{len(candidate.violations)} violation(s)"
                )


class TestTheFixtureScenarioIsUnchanged:
    """The demo's own result, pinned.

    Everything above is new coverage; this is the guard that the coverage did
    not come at the cost of the scenario the demo narrates.
    """

    def test_the_night_train_frontier_is_a_and_b2(self, night_train_state: ProductionState) -> None:
        result = recover(night_train_state, _event(*CASES[0]))
        frontier = sorted(c.plan.id for c in result.evaluated if c.pareto_optimal)
        assert frontier == ["plan-A", "plan-B2"], frontier


class TestTotalLossIsStillAnswered:
    """The widest disruption the fixture can express.

    `equipment.failed` is the one event type with no window: losing a camera
    body is treated as a permanent block, not a block until Tuesday. CAM-01 is
    on all thirteen scenes, so failing it blocks the entire remaining
    production — blast radius 1.0.

    Worth pinning because it is the case most likely to break quietly. A
    planner that assumed some scenes always survive would divide by zero, throw
    or return nonsense here; the honest outcomes are a valid plan or an
    explicit refusal, and this asserts it is one of those.
    """

    def test_it_blocks_the_whole_production(self, night_train_state: ProductionState) -> None:
        result = recover(
            night_train_state,
            _event("equipment.failed", {"equipment_id": "CAM-01", "reason": "Body down"}),
        )
        assert len(result.impact.directly_affected_scene_ids) == len(night_train_state.scenes)
        assert result.impact.blast_radius == pytest.approx(1.0)

    def test_it_still_returns_something_a_producer_can_act_on(
        self, night_train_state: ProductionState
    ) -> None:
        result = recover(
            night_train_state,
            _event("equipment.failed", {"equipment_id": "CAM-01", "reason": "Body down"}),
        )
        assert result.evaluated, "total loss produced no candidates at all"
        assert any(c.valid for c in result.evaluated), (
            "total loss produced only invalid candidates; a refusal is defensible "
            "but it must be reported, not returned as an empty list"
        )
