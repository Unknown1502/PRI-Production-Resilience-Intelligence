"""Orchestration for one recovery run.

Sits between the HTTP route and the pure engine: loads the state and the event,
runs the deterministic loop, streams each stage to connected clients, persists
the session and its candidates, and — when the agent is enabled — asks Gemini
for the strategy hints and the explanation.

The agent is strictly optional.  ``PRI_AGENT_ENABLED=false``, a missing
credential or any exception from the model drops through to the deterministic
narrator, and the response says ``mode: "deterministic"`` so the UI can show
the badge.  A quota error must cost the demo a paragraph of prose, not the
recovery.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

import structlog

from pri.api.schemas import RecoveryOut, recovery_out
from pri.api.stream import Stage, StreamBroker
from pri.domain.models import DisruptionEvent, RecoveryResult
from pri.engine.simulation.narrate import explain, recommend, summarise_tradeoffs
from pri.engine.simulation.replan import recover

if TYPE_CHECKING:
    from pri.config import Settings
    from pri.domain.models import ProductionState
    from pri.persistence.repository import PriRepository

__all__ = ["run_recovery"]

_log = structlog.get_logger(__name__)


async def run_recovery(
    repo: PriRepository,
    broker: StreamBroker,
    settings: Settings,
    production_id: str,
    event_id: str,
    strategy_hints: list[str] | None = None,
) -> RecoveryOut:
    """Produce, persist and narrate recovery options for one event.

    Inputs:
        repo:           Persistence.
        broker:         SSE fan-out.
        settings:       Application settings; ``pri_agent_enabled`` decides the
                        narration path.
        production_id:  The production being recovered.
        event_id:       An event already recorded via ``record_event``.
        strategy_hints: Caller-supplied families.  When the agent is enabled and
                        this is ``None``, the agent chooses.

    Outputs:
        The assembled :class:`RecoveryOut`.

    Failure modes:
        Raises :exc:`~pri.persistence.errors.ProductionNotFoundError` if the
        production has no state, or ``ValueError`` if the event is unknown.
    """
    state = await repo.get_current_state(production_id)
    event = await _load_event(repo, production_id, event_id)

    broker.publish(
        production_id,
        Stage.EVENT_RECEIVED,
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "severity": event.severity,
            "source": event.source,
        },
    )

    mode: Literal["agent", "deterministic"] = "deterministic"
    tool_calls: list[dict[str, Any]] = []
    agent_output = None

    if settings.pri_agent_enabled:
        agent_output = await _try_agent(settings, state, event, strategy_hints)
        if agent_output is not None:
            mode = "agent"
            strategy_hints = list(agent_output.strategy_hints_used) or strategy_hints
            tool_calls = [tc.model_dump(mode="json") for tc in agent_output.tool_calls]

    result = await asyncio.to_thread(recover, state, event, strategy_hints)

    _publish_pipeline(broker, production_id, result)

    await _persist(repo, production_id, event_id, result)

    chosen = recommend(result)
    if agent_output is not None and agent_output.explanation:
        explanation = agent_output.explanation
        tradeoffs = agent_output.tradeoff_summary or summarise_tradeoffs(
            result, state.production.currency
        )
        recommended_id = agent_output.recommended_plan_id or (chosen.plan.id if chosen else None)
    else:
        explanation = explain(result, state.production.currency)
        tradeoffs = summarise_tradeoffs(result, state.production.currency)
        recommended_id = chosen.plan.id if chosen else None

    if recommended_id is not None:
        broker.publish(
            production_id,
            Stage.AWAITING_APPROVAL,
            {"session_id": result.session_id, "recommended_plan_id": recommended_id},
        )

    return recovery_out(
        result,
        explanation=explanation,
        tradeoff_summary=tradeoffs,
        mode=mode,
        recommended_plan_id=recommended_id,
        tool_calls=tool_calls,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _load_event(repo: PriRepository, production_id: str, event_id: str) -> DisruptionEvent:
    """Rehydrate a stored event, or explain why it cannot be recovered from."""
    row = await repo.get_event(event_id)
    if row is None:
        raise ValueError(f"Event {event_id!r} has not been recorded")
    if str(row["production_id"]) != production_id:
        raise ValueError(f"Event {event_id!r} belongs to production {row['production_id']!r}")
    payload = row["payload"]
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    return DisruptionEvent(
        event_id=str(row["event_id"]),
        production_id=str(row["production_id"]),
        event_type=str(row["event_type"]),
        occurred_at=row["occurred_at"],
        source=str(row["source"]),
        severity=float(row["severity"]),
        payload=payload or {},
    )


def _publish_pipeline(broker: StreamBroker, production_id: str, result: RecoveryResult) -> None:
    """Replay the completed run onto the stream, stage by stage.

    The loop itself is synchronous and fast — a few milliseconds — so emitting
    afterwards rather than interleaving costs the UI nothing and keeps the
    engine free of any knowledge that a stream exists.
    """
    impact = result.impact
    broker.publish(
        production_id,
        Stage.IMPACT_COMPUTED,
        {
            "directly_affected_scene_ids": list(impact.directly_affected_scene_ids),
            "downstream_scene_ids": list(impact.downstream_scene_ids),
            "affected_days": [d.isoformat() for d in impact.affected_days],
            "blast_radius": impact.blast_radius,
            "downstream_dependency_count": impact.downstream_dependency_count,
        },
    )

    for trace in result.rounds:
        if trace.round_number == 1:
            broker.publish(
                production_id,
                Stage.CANDIDATES_GENERATED,
                {
                    "session_id": result.session_id,
                    "plan_ids": list(trace.generated_plan_ids),
                    "count": len(trace.generated_plan_ids),
                    "note": trace.note,
                },
            )
        for plan_id in trace.invalid_plan_ids:
            plan = next(
                (ep for ep in result.evaluated if ep.plan.id == plan_id),
                None,
            )
            violation = (
                next((v for v in plan.violations if v.severity == "HARD"), None) if plan else None
            )
            broker.publish(
                production_id,
                Stage.CANDIDATE_INVALID,
                {
                    "plan_id": plan_id,
                    "label": plan.plan.label if plan else plan_id,
                    "rule_code": violation.code if violation else None,
                    "observed": violation.observed if violation else None,
                    "required": violation.required if violation else None,
                    "message": violation.message if violation else None,
                },
            )
        if trace.repaired_plan_ids:
            broker.publish(
                production_id,
                Stage.REPLANNING,
                {"repairing": list(trace.invalid_plan_ids), "note": trace.note},
            )
            for plan_id in trace.repaired_plan_ids:
                plan = next((ep for ep in result.evaluated if ep.plan.id == plan_id), None)
                if plan is not None and plan.valid:
                    broker.publish(
                        production_id,
                        Stage.CANDIDATE_VALID,
                        {"plan_id": plan_id, "label": plan.plan.label},
                    )


async def _persist(
    repo: PriRepository,
    production_id: str,
    event_id: str,
    result: RecoveryResult,
) -> None:
    """Store the session and its candidates under the engine's session id.

    The engine generates the session id so the whole run — stream frames
    included — refers to one identifier before anything is written.
    """
    await repo.create_session_with_id(
        result.session_id, production_id, event_id, result.base_version
    )
    await repo.add_candidates(result.session_id, list(result.evaluated))
    await repo.append_audit(
        production_id,
        "system",
        "recovery.completed",
        result.session_id,
        {
            "event_id": event_id,
            "base_version": result.base_version,
            "candidates": [ep.plan.label for ep in result.evaluated],
            "pareto": [ep.plan.label for ep in result.pareto_plans],
        },
    )


async def _try_agent(
    settings: Settings,
    state: ProductionState,
    event: DisruptionEvent,
    strategy_hints: list[str] | None,
) -> Any:
    """Ask the ADK agent for hints and prose, or return ``None`` on any failure.

    Deliberately broad: quota, credentials, network, a model that returns
    nonsense — every one of them means "narrate deterministically instead",
    and none of them should reach the client as a 500.
    """
    try:
        from pri.agent.service import run_agent_recovery

        return await run_agent_recovery(settings, state, event, strategy_hints)
    except Exception as exc:
        _log.warning("agent_unavailable", error=str(exc), exc_type=type(exc).__name__)
        return None
