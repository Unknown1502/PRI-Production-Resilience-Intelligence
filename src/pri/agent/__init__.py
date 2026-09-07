"""Google ADK agent package.

Native ``google-adk`` and Gemini only. No LangChain, LangGraph, CrewAI,
AutoGen, OpenAI, Anthropic, Bedrock or Azure AI appears anywhere in this
package or its imports — the competition rules forbid all non-Google AI
tooling, and ``tests/agent/test_compliance.py`` asserts it.
"""

from pri.agent.root_agent import AGENT_NAME, build_root_agent
from pri.agent.service import (
    AgentRecoveryOutput,
    AgentUnavailableError,
    run_agent_recovery,
)
from pri.agent.tools import AgentToolbox, ToolCallTrace

__all__ = [
    "AGENT_NAME",
    "AgentRecoveryOutput",
    "AgentToolbox",
    "AgentUnavailableError",
    "ToolCallTrace",
    "build_root_agent",
    "run_agent_recovery",
]
