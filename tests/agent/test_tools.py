"""The agent's toolbox — what Gemini can and cannot reach.

PROMPT 12 requires that the agent calls ``generate_and_evaluate`` before it
explains anything, and that no number in its explanation is absent from a tool
result. Both are properties of the toolbox, so they are tested here without a
model in the loop.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pri.agent.root_agent import AGENT_NAME, build_root_agent, load_instruction
from pri.agent.tools import AgentToolbox

if TYPE_CHECKING:
    from pri.domain.models import DisruptionEvent, ProductionState


class TestToolbox:
    def test_impact_reports_the_blocked_scenes(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        impact = toolbox.get_impact()
        assert set(impact["directly_affected_scene_ids"]) == {"S17", "S18", "S21"}

    def test_generate_and_evaluate_runs_the_deterministic_planner(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        payload = toolbox.generate_and_evaluate(["DEFER", "SWAP", "RELOCATE"])

        labels = [c["label"] for c in payload["candidates"]]
        assert labels == ["A", "B", "C", "B2"]
        assert payload["pareto_plan_ids"] == ["plan-A", "plan-B2"]

    def test_violations_reach_the_model_verbatim(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        """The agent is told to quote these strings, so they must not be reshaped."""
        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        payload = toolbox.generate_and_evaluate([])
        plan_b = next(c for c in payload["candidates"] if c["label"] == "B")
        violation = next(v for v in plan_b["violations"] if v["severity"] == "HARD")

        assert violation["code"] == "C001"
        assert violation["observed"] == "9.0h"
        assert violation["required"] == ">= 10.0h"

    def test_plan_comparison_refuses_before_the_planner_has_run(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        assert "error" in toolbox.get_plan_comparison()

    def test_approval_of_an_unknown_plan_is_refused(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        """The model cannot queue a plan the planner never produced."""
        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        toolbox.generate_and_evaluate([])
        assert "error" in toolbox.request_approval("plan-INVENTED", "because I said so")

    def test_every_call_is_traced(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        toolbox.get_production_state()
        toolbox.get_impact()
        toolbox.generate_and_evaluate(["SWAP"])

        assert [call.name for call in toolbox.calls] == [
            "get_production_state",
            "get_impact",
            "generate_and_evaluate",
        ]
        assert toolbox.strategy_hints_used == ("SWAP",)


class TestContainment:
    """Gemini selects families. It cannot author a plan."""

    def test_hints_only_filter_families(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        payload = toolbox.generate_and_evaluate(["DEFER"])
        assert {c["strategy_family"] for c in payload["candidates"]} == {"DEFER"}

    def test_nonsense_hints_cannot_invent_a_family(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        payload = toolbox.generate_and_evaluate(["MAKE_IT_CHEAPER", "ASK_THE_STUDIO"])

        # Unknown names fall back to all four families. Round-two repairs are
        # labelled by what they repaired, not by a family, so they are excluded.
        generated = [c for c in payload["candidates"] if not c["label"].endswith("2")]
        assert {c["strategy_family"] for c in generated} <= {
            "DEFER",
            "SWAP",
            "RELOCATE",
            "COMPRESS",
        }

    def test_every_number_the_model_can_quote_came_from_the_engine(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        """PROMPT 12's real constraint, checked from the other direction.

        The agent is instructed to quote only numbers a tool returned. This
        asserts the tool payload is the *only* place those numbers could have
        come from, by confirming every score in it matches a re-run of the
        deterministic planner exactly.
        """
        from pri.engine.simulation.replan import recover

        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        payload = toolbox.generate_and_evaluate([])
        independent = recover(night_train_state, loc04_blocked_event)

        by_label = {ep.plan.label: ep for ep in independent.evaluated}
        for candidate in payload["candidates"]:
            expected = by_label[candidate["label"]]
            assert expected.score is not None
            assert candidate["score"]["schedule_delay_days"] == (expected.score.schedule_delay_days)
            assert candidate["score"]["incremental_cost"] == str(expected.score.incremental_cost)
            assert candidate["score"]["operational_risk"] == expected.score.operational_risk


class TestRootAgent:
    def test_agent_builds_with_six_tools(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        agent = build_root_agent("gemini-2.5-flash", toolbox)

        assert agent.name == AGENT_NAME
        assert agent.model == "gemini-2.5-flash"
        assert len(agent.tools) == 6

    def test_it_is_a_single_agent_not_a_hierarchy(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        """More agents is not more intelligence; every hop is a chance to
        paraphrase a number, and the numbers are the product."""
        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        agent = build_root_agent("gemini-2.5-flash", toolbox)
        assert not agent.sub_agents

    def test_the_model_id_is_never_hard_coded(
        self, night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
    ) -> None:
        toolbox = AgentToolbox(night_train_state, loc04_blocked_event)
        assert build_root_agent("gemini-9-imaginary", toolbox).model == "gemini-9-imaginary"

    def test_the_instruction_states_the_architecture_law(self) -> None:
        # Collapsed, because the file is hard-wrapped and the law must be
        # matched as a sentence rather than as whatever fits on one line.
        instruction = " ".join(load_instruction().lower().split())
        assert "you do not compute numbers" in instruction
        assert "quote only numbers returned by tools" in instruction
        assert "never claim you discovered your own mistake" in instruction
        assert "the planner generated a repair" in instruction

    def test_the_instruction_names_all_four_families(self) -> None:
        instruction = load_instruction()
        for family in ("DEFER", "SWAP", "RELOCATE", "COMPRESS"):
            assert family in instruction

    def test_the_instruction_forbids_rounding(self) -> None:
        """A figure rounded into a different figure is a misquoted figure."""
        assert re.search(r"never round", load_instruction(), re.IGNORECASE)
