"""The tools the agent is allowed to call.

Each one is a thin wrapper over the deterministic engine, called in-process —
no HTTP hop, so a tool result cannot drift from what the engine actually
computed.  The wrappers exist to shape data for a language model, not to add
behaviour: none of them decides anything.

The containment that matters is in ``generate_and_evaluate``.  It takes
``strategy_hints`` and passes them to the generator; that is the entire surface
through which the model influences a plan.  It cannot construct a move, edit a
schedule, or return a number the engine did not produce.

Tool functions must be plain callables with typed signatures and docstrings:
ADK builds the function-calling schema from exactly those, so the docstring
below each ``def`` is a prompt, not a comment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pri.engine.constraints.validator import load_policy
from pri.engine.graph.dependency import impact_of
from pri.engine.simulation.replan import recover

if TYPE_CHECKING:
    from pri.domain.models import DisruptionEvent, ProductionState, RecoveryResult

__all__ = ["AgentToolbox", "ToolCallTrace"]


@dataclass
class ToolCallTrace:
    """One recorded tool invocation, surfaced in the UI.

    Showing the real call sequence is the difference between "an AI said this"
    and "here is what it looked at".

    Inputs:
        name:      Tool name as the model called it.
        arguments: Arguments the model supplied.
        summary:   One line describing what came back.
        ok:        Whether the call succeeded.
    """

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    ok: bool = True

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        """Render for JSON transport (mirrors the pydantic method the API expects)."""
        del mode
        return {
            "name": self.name,
            "arguments": self.arguments,
            "summary": self.summary,
            "ok": self.ok,
        }


class AgentToolbox:
    """Engine access for one recovery run, bound to one state and one event.

    Binding the state up front is deliberate: the model names strategy families,
    not productions.  It cannot reach a different show, an older version, or a
    disruption it was not asked about.

    Inputs (constructor):
        state: The production state to recover.
        event: The disruption being recovered from.
    """

    def __init__(self, state: ProductionState, event: DisruptionEvent) -> None:
        self._state = state
        self._event = event
        self._result: RecoveryResult | None = None
        self.calls: list[ToolCallTrace] = []

    # ------------------------------------------------------------------
    # Results the caller reads back after the run
    # ------------------------------------------------------------------

    @property
    def result(self) -> RecoveryResult | None:
        """The recovery run the model triggered, if it triggered one."""
        return self._result

    @property
    def strategy_hints_used(self) -> tuple[str, ...]:
        """The families the model actually asked for."""
        for call in self.calls:
            if call.name == "generate_and_evaluate":
                hints = call.arguments.get("strategy_hints") or []
                return tuple(str(h) for h in hints)
        return ()

    def as_tool_functions(self) -> list[Any]:
        """Return the bound callables to hand to the ADK agent."""
        return [
            self.get_production_state,
            self.get_impact,
            self.get_constraints,
            self.generate_and_evaluate,
            self.get_plan_comparison,
            self.request_approval,
        ]

    def _record(self, name: str, arguments: dict[str, Any], summary: str, ok: bool = True) -> None:
        self.calls.append(ToolCallTrace(name=name, arguments=arguments, summary=summary, ok=ok))

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def get_production_state(self) -> dict[str, Any]:
        """Summarise the production being recovered.

        Returns the title, currency, shoot window, reserve days, and one entry
        per shooting day with its date, location, call and wrap times, and the
        scenes on it. Use this to understand the shape of the schedule before
        deciding which recovery strategies are worth exploring.
        """
        state = self._state
        summary: dict[str, Any] = {
            "production_id": state.production.id,
            "title": state.production.title,
            "currency": state.production.currency,
            "version": state.version,
            "shoot_start": state.production.shoot_start.isoformat(),
            "shoot_end": state.production.shoot_end.isoformat(),
            "reserve_days": [d.isoformat() for d in state.production.reserve_days],
            "scene_count": len(state.scenes),
            "days": [
                {
                    "date": day.date.isoformat(),
                    "location_id": day.location_id,
                    "call": day.call_time.strftime("%H:%M"),
                    "wrap": day.wrap_time.strftime("%H:%M"),
                    "scenes": list(day.scene_ids),
                }
                for day in sorted(state.schedule.days, key=lambda d: d.date)
            ],
        }
        self._record(
            "get_production_state",
            {},
            f"{summary['title']} v{summary['version']}, "
            f"{summary['scene_count']} scenes, {len(summary['days'])} days",
        )
        return summary

    def get_impact(self) -> dict[str, Any]:
        """Compute what the disruption broke.

        Returns the directly affected scenes, the downstream scenes blocked by
        prerequisite dependencies, the cast, crew, locations and equipment
        involved, the affected shooting days, and the blast radius as a
        fraction of the remaining schedule. Call this first.
        """
        report = impact_of(self._state, self._event)
        payload = report.model_dump(mode="json")
        self._record(
            "get_impact",
            {},
            f"{len(report.directly_affected_scene_ids)} scenes blocked, "
            f"{report.downstream_dependency_count} downstream, "
            f"blast radius {report.blast_radius:.0%}",
        )
        return payload

    def get_constraints(self) -> dict[str, Any]:
        """Return the active scheduling policy.

        These are the rules a plan must satisfy: crew turnaround minimum,
        maximum daily hours, cast availability, location permits and double
        booking, equipment rental windows, prerequisite ordering, and
        INT/EXT and time-of-day matching. Rule codes run C001 to C010.
        """
        policy = load_policy()
        self._record("get_constraints", {}, f"{len(policy)} policy groups")
        return dict(policy)

    def generate_and_evaluate(self, strategy_hints: list[str]) -> dict[str, Any]:
        """Generate recovery plans, validate them, repair failures, and score them.

        Pass the strategy families you want explored, in priority order, chosen
        from DEFER, SWAP, RELOCATE and COMPRESS. An empty list explores all
        four. Unknown names are ignored.

        This runs the deterministic planner: it enumerates plans from the
        families you named, applies each to the schedule, validates it against
        every constraint rule, generates a repair for anything that fails, and
        scores the survivors. Returns every candidate — valid and invalid —
        with its moves, violations, scores and Pareto flag, plus a per-round
        trace of what was generated and what failed.

        You cannot author a plan. This is the only way plans come into
        existence, and the numbers it returns are the only numbers you may
        quote.
        """
        result = recover(self._state, self._event, strategy_hints or None)
        self._result = result

        invalid = [ep for ep in result.evaluated if not ep.valid]
        self._record(
            "generate_and_evaluate",
            {"strategy_hints": list(strategy_hints)},
            f"{len(result.evaluated)} candidates, {len(invalid)} invalid, "
            f"{len(result.pareto_plans)} on the frontier",
        )
        return _result_payload(result)

    def get_plan_comparison(self) -> dict[str, Any]:
        """Read back the scored candidates from the last generate_and_evaluate call.

        Returns each plan's label, validity, moves, constraint violations, the
        six score dimensions, and whether it is Pareto-optimal. Use this when
        you are writing the comparison and need the figures again.
        """
        if self._result is None:
            self._record(
                "get_plan_comparison",
                {},
                "no candidates yet - generate_and_evaluate has not been called",
                ok=False,
            )
            return {"error": "No plans have been generated yet. Call generate_and_evaluate first."}
        self._record(
            "get_plan_comparison", {}, f"{len(self._result.evaluated)} candidates returned"
        )
        return _result_payload(self._result)

    def request_approval(self, plan_id: str, justification: str) -> dict[str, Any]:
        """Queue a plan for the producer's approval.

        This does not approve anything and does not change the schedule. It
        records which plan you are recommending and why, so the producer sees
        it highlighted when they open the approval panel. A human makes the
        decision.
        """
        known = {ep.plan.id for ep in self._result.evaluated} if self._result is not None else set()
        if plan_id not in known:
            self._record(
                "request_approval",
                {"plan_id": plan_id, "justification": justification},
                f"unknown plan {plan_id}",
                ok=False,
            )
            return {"error": f"No candidate with id {plan_id!r} in this session."}
        self._record(
            "request_approval",
            {"plan_id": plan_id, "justification": justification},
            f"queued {plan_id} for human approval",
        )
        return {"queued": True, "plan_id": plan_id, "awaiting": "producer approval"}


# ---------------------------------------------------------------------------
# Payload shaping
# ---------------------------------------------------------------------------


def _result_payload(result: RecoveryResult) -> dict[str, Any]:
    """Flatten a recovery result into something a model reads well.

    Violations keep their ``observed`` and ``required`` strings verbatim
    because the agent is instructed to quote them, and a reshaped string is a
    misquoted one.
    """
    return {
        "session_id": result.session_id,
        "base_version": result.base_version,
        "candidates": [
            {
                "plan_id": ep.plan.id,
                "label": ep.plan.label,
                "strategy_family": ep.plan.rationale_hint,
                "valid": ep.valid,
                "pareto_optimal": ep.pareto_optimal,
                "moves": [m.model_dump(mode="json") for m in ep.plan.moves],
                "violations": [
                    {
                        "code": v.code,
                        "severity": v.severity,
                        "message": v.message,
                        "observed": v.observed,
                        "required": v.required,
                    }
                    for v in ep.violations
                ],
                "score": ep.score.model_dump(mode="json") if ep.score else None,
            }
            for ep in result.evaluated
        ],
        "rounds": [
            {
                "round": t.round_number,
                "generated": list(t.generated_plan_ids),
                "invalid": list(t.invalid_plan_ids),
                "failed_rule_codes": list(t.failed_rule_codes),
                "repairs": list(t.repaired_plan_ids),
                "note": t.note,
            }
            for t in result.rounds
        ],
        "pareto_plan_ids": [ep.plan.id for ep in result.pareto_plans],
    }
