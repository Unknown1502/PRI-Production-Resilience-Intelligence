"""The ADK root agent.

One agent, not five.  A supervisor delegating to a scheduler delegating to a
cost analyst would look more impressive in a diagram and would be strictly
worse: every hop is a chance to paraphrase a number, and the numbers are the
product.  There is exactly one thing here a language model is genuinely better
at than code — choosing which strategy families are worth exploring, and
explaining the result to a person — so there is exactly one agent.

Built against ``google-adk`` 2.8.0.  ``LlmAgent`` derives its function-calling
schema from each tool's signature and docstring, so
:class:`~pri.agent.tools.AgentToolbox`'s docstrings are part of the prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent

if TYPE_CHECKING:
    from pri.agent.tools import AgentToolbox

__all__ = ["AGENT_NAME", "build_root_agent", "load_instruction"]

AGENT_NAME = "pri_recovery_analyst"

_INSTRUCTION_PATH = Path(__file__).parent / "prompts" / "root.md"

#: Used only if the instruction file is missing from a container image. Keeping
#: the architecture law in the fallback matters more than keeping the prose.
_FALLBACK_INSTRUCTION = (
    "You do not compute numbers. Interpret the disruption, choose which recovery "
    "strategy families to explore (DEFER, SWAP, RELOCATE, COMPRESS), call "
    "generate_and_evaluate, and explain the deterministic results. Quote only "
    "numbers returned by tools. State rule codes and observed vs required values "
    "verbatim. Never say you found your own mistake: the candidate failed "
    "deterministic validation and the planner generated a repair."
)


def load_instruction(path: Path | None = None) -> str:
    """Read the root agent's instruction from ``agent/prompts/root.md``.

    Inputs:
        path: Override the instruction file location.

    Outputs:
        The instruction text, or a compact fallback if the file is absent.
    """
    resolved = path if path is not None else _INSTRUCTION_PATH
    if not resolved.exists():
        return _FALLBACK_INSTRUCTION
    return resolved.read_text(encoding="utf-8")


def build_root_agent(model: str, toolbox: AgentToolbox) -> LlmAgent:
    """Construct the recovery agent bound to one production's toolbox.

    Inputs:
        model:   Gemini model id, from ``Settings.gemini_model``.  Never
                 hard-coded — the deployed model is an operational choice.
        toolbox: The engine access for this run.

    Outputs:
        A configured :class:`LlmAgent`.

    Failure modes:
        Raises ``pydantic.ValidationError`` if ADK rejects the configuration —
        for example a tool whose signature it cannot turn into a schema.
    """
    return LlmAgent(
        name=AGENT_NAME,
        model=model,
        description=(
            "Interprets a film-production disruption, chooses which recovery "
            "strategies to explore, and explains the deterministic planner's "
            "results to a producer."
        ),
        instruction=load_instruction(),
        tools=list(toolbox.as_tool_functions()),
    )
