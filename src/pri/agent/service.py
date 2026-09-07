"""Running the agent, and surviving it not running.

``run_agent_recovery`` drives one turn of the ADK agent and returns its strategy
hints, its recommendation and its prose.  It is called from the API behind
``PRI_AGENT_ENABLED``, and every failure path returns to the deterministic
narrator instead of propagating — quota, credentials, a model that never calls a
tool.  The hosted demo has to work at 2am when a judge opens it and nobody is
watching the billing dashboard.

The guarantee worth stating plainly: the agent cannot change what happens, only
what is said about it.  The plans it reports were generated, validated and
scored by :mod:`pri.engine.simulation`, and the API re-runs that same
deterministic loop afterwards regardless of what the model returned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from pri.agent.root_agent import build_root_agent
from pri.agent.tools import AgentToolbox, ToolCallTrace

if TYPE_CHECKING:
    from pri.config import Settings
    from pri.domain.models import DisruptionEvent, ProductionState, RecoveryResult

__all__ = ["AgentRecoveryOutput", "AgentUnavailableError", "run_agent_recovery"]

_log = structlog.get_logger(__name__)

_APP_NAME = "pri"
_USER_ID = "producer"


class AgentUnavailableError(RuntimeError):
    """Raised when the agent cannot run and the caller should fall back."""


@dataclass
class AgentRecoveryOutput:
    """What one agent turn produced.

    Inputs:
        session_id:          The recovery session the agent triggered.
        strategy_hints_used: The families it asked the generator to expand.
        recommended_plan_id: The plan it recommends, if it named one.
        explanation:         Producer-facing prose.
        tradeoff_summary:    One-line comparison across the options.
        tool_calls:          The real call sequence, for the UI trace panel.
        result:              The deterministic recovery result it triggered.
    """

    session_id: str | None
    strategy_hints_used: tuple[str, ...]
    recommended_plan_id: str | None
    explanation: str
    tradeoff_summary: str
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    result: RecoveryResult | None = None


def _configure_genai_env(settings: Settings) -> None:
    """Point the Google GenAI SDK at Vertex or the Developer API.

    ADK reads these from the environment rather than taking them as arguments,
    so the settings object is projected onto ``os.environ`` before the client is
    constructed.  Only values that are actually configured are set: writing an
    empty ``GOOGLE_API_KEY`` would break Vertex credential discovery.
    """
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = (
        "true" if settings.google_genai_use_vertexai else "false"
    )
    if settings.google_genai_use_vertexai:
        if settings.google_cloud_project:
            os.environ["GOOGLE_CLOUD_PROJECT"] = settings.google_cloud_project
        if settings.google_cloud_location:
            os.environ["GOOGLE_CLOUD_LOCATION"] = settings.google_cloud_location
    elif settings.google_api_key is not None:
        key = settings.google_api_key.get_secret_value()
        if key:
            os.environ["GOOGLE_API_KEY"] = key


async def run_agent_recovery(
    settings: Settings,
    state: ProductionState,
    event: DisruptionEvent,
    strategy_hints: list[str] | None = None,
) -> AgentRecoveryOutput:
    """Ask Gemini to interpret the disruption and explain the planner's results.

    Inputs:
        settings:       Application settings; supplies the model id and the
                        Vertex/Developer routing.
        state:          The current production state.
        event:          The disruption to recover from.
        strategy_hints: Families the caller wants explored.  Passed to the model
                        as a suggestion; the model may choose differently, and
                        either way only the enumerated families are expandable.

    Outputs:
        An :class:`AgentRecoveryOutput`.

    Failure modes:
        Raises :class:`AgentUnavailableError` when the model never called
        ``generate_and_evaluate`` — an explanation with no tool call behind it
        is exactly the thing this architecture exists to prevent, so it is
        discarded rather than shown.
        Propagates SDK exceptions (quota, auth, network); the API layer treats
        any of them as "narrate deterministically".
    """
    _configure_genai_env(settings)

    toolbox = AgentToolbox(state, event)
    agent = build_root_agent(settings.gemini_model, toolbox)
    session_service = InMemorySessionService()
    runner = Runner(app_name=_APP_NAME, agent=agent, session_service=session_service)

    session = await session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID)
    message = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=_opening_turn(event, strategy_hints))],
    )

    chunks: list[str] = []
    try:
        async for chunk in runner.run_async(
            user_id=_USER_ID,
            session_id=session.id,
            new_message=message,
        ):
            text = _text_of(chunk)
            if text and chunk.is_final_response():
                chunks.append(text)
    finally:
        await runner.close()

    if toolbox.result is None:
        raise AgentUnavailableError(
            "The model produced an explanation without calling generate_and_evaluate; "
            "discarding it and narrating deterministically instead."
        )

    explanation = "\n\n".join(c.strip() for c in chunks if c.strip())
    result = toolbox.result

    _log.info(
        "agent_recovery_complete",
        model=settings.gemini_model,
        tool_calls=[c.name for c in toolbox.calls],
        hints=toolbox.strategy_hints_used,
        candidates=len(result.evaluated),
    )

    return AgentRecoveryOutput(
        session_id=result.session_id,
        strategy_hints_used=toolbox.strategy_hints_used,
        recommended_plan_id=_recommended_from_calls(toolbox),
        explanation=explanation,
        tradeoff_summary="",
        tool_calls=list(toolbox.calls),
        result=result,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _opening_turn(event: DisruptionEvent, strategy_hints: list[str] | None) -> str:
    """The single user turn that starts the agent working."""
    lines = [
        f"A {event.event_type} disruption has been reported on the production.",
        f"Source: {event.source}. Severity: {event.severity:.2f}.",
        f"Event id: {event.event_id}.",
    ]
    detail = ", ".join(f"{k}={v}" for k, v in sorted(event.payload.items()))
    if detail:
        lines.append(f"Payload: {detail}")
    if strategy_hints:
        lines.append(
            "The operator suggests exploring these families first: "
            + ", ".join(strategy_hints)
            + ". Use your own judgement if the impact says otherwise."
        )
    lines.append(
        "Work out what broke, generate and evaluate recovery options, and explain "
        "the results to the producer."
    )
    return "\n".join(lines)


def _text_of(event_chunk: object) -> str:
    """Extract plain text from an ADK event, if it carries any."""
    content = getattr(event_chunk, "content", None)
    if content is None:
        return ""
    parts = getattr(content, "parts", None) or []
    return "".join(getattr(part, "text", "") or "" for part in parts)


def _recommended_from_calls(toolbox: AgentToolbox) -> str | None:
    """The plan the model queued for approval, if it queued one."""
    for call in reversed(toolbox.calls):
        if call.name == "request_approval" and call.ok:
            plan_id = call.arguments.get("plan_id")
            if isinstance(plan_id, str):
                return plan_id
    return None
