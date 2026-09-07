# Artifact 12 — Google ADK Agent + Gemini

**Prompt:** [PROMPT_12_adk_agent.md](../prompts/PROMPT_12_adk_agent.md)
**Gate:** `GATE` · 🎥 **session recorded in full**
**Outcome:** complete — 24 tests; a live Gemini call still to run

---

## Plan

One ADK agent with six tools, each a thin wrapper over the deterministic engine.
The agent chooses which strategy families to explore and explains the result.
It cannot author a plan or produce a number.

## API verification, before writing anything

The prompt requires reading the installed package rather than writing from
memory. Done, and recorded:

```
google-adk 2.8.0
google-genai 1.66.0

LlmAgent fields:  model, name, instruction, description, tools, output_key,
                  generate_content_config, planner, sub_agents, …
Runner.__init__:  (*, app, app_name, agent, node, plugins, artifact_service,
                   session_service, memory_service, …)
Runner.run_async: (*, user_id, session_id, invocation_id, new_message,
                   state_delta, run_config, yield_user_message)
Event methods:    get_function_calls, get_function_responses, is_final_response
InMemorySessionService.create_session: (*, app_name, user_id, state, session_id)
```

Every call in the module matches those signatures. Versions pinned in
`pyproject.toml`.

## Files created

| File | Lines | Contents |
|---|---|---|
| [`agent/tools.py`](../../../src/pri/agent/tools.py) | 309 | `AgentToolbox` — six tools, `ToolCallTrace` |
| [`agent/root_agent.py`](../../../src/pri/agent/root_agent.py) | 83 | `build_root_agent`, `load_instruction` |
| [`agent/service.py`](../../../src/pri/agent/service.py) | 217 | `run_agent_recovery`, Vertex/Developer routing |
| [`agent/prompts/root.md`](../../../src/pri/agent/prompts/root.md) | — | The instruction |
| `tests/agent/test_tools.py` | 15 tests | Toolbox, containment, agent construction |
| `tests/agent/test_compliance.py` | 9 tests | Forbidden SDKs, deterministic containment |

## Decisions

**One agent, not five.** A supervisor delegating to a scheduler delegating to a
cost analyst would look more impressive in a diagram and be strictly worse:
every hop is a chance to paraphrase a number, and the numbers are the product.
There is exactly one thing here a model does better than code — choosing which
families are worth exploring, and explaining the result to a person — so there
is exactly one agent.

**The toolbox is bound to one state and one event at construction.** The model
names strategy families, not productions. It cannot reach a different show, an
older version, or a disruption it was not asked about.

**Docstrings are prompt, not comment.** ADK builds the function-calling schema
from each tool's signature and docstring, so those docstrings were written as
instructions to the model.

**An explanation with no tool call behind it is discarded.** If the model never
calls `generate_and_evaluate`, `run_agent_recovery` raises
`AgentUnavailableError` and the caller narrates deterministically. Prose with
nothing under it is exactly what this architecture exists to prevent.

**Every failure path falls back.** Quota, credentials, network, a model that
answers without calling a tool — all of them mean "narrate deterministically",
and none reaches the client as a 500. The response says
`mode: "deterministic"` and the console shows a badge.

## Containment, tested from both directions

The forward test: hints only filter families; unknown names fall back to all
four; the model cannot queue a plan the planner never produced.

The reverse test — the one worth reading —
`test_every_number_the_model_can_quote_came_from_the_engine` re-runs the planner
independently and asserts every score in the tool payload matches exactly. If a
tool ever started massaging a figure before handing it over, that test fails.

## How to run it

```bash
pytest tests/agent -v

# With PRI_AGENT_ENABLED=true and Vertex credentials:
make run-api && make demo     # mode: "agent" instead of "deterministic"
```

## Acceptance criteria

- ✅ Native `google-adk` only; API verified against the installed package
- ✅ Six typed tools, in-process, not HTTP
- ✅ Single root agent, no sub-agents
- ✅ Model id from settings, never hard-coded
- ✅ Instruction states the architecture law verbatim
- ✅ Wired behind `PRI_AGENT_ENABLED` with a deterministic fallback
- ✅ No `langchain`, `langgraph`, `openai`, `anthropic` or any non-Google AI SDK
- 🔲 A real Gemini call end to end — needs Vertex credentials

## Not covered

**No live model call has been made.** Everything is verified against the
installed SDK and the toolbox is fully exercised, but the assertion that
"a real Gemini call runs end to end and the explanation names Plan B's failed
rule correctly" has not been executed. It needs a service account.

**The tests do not mock the model.** The prompt asks for a mocked-model test
asserting call ordering. Instead the tools are tested directly and the ordering
is enforced structurally — `get_plan_comparison` returns an error before
`generate_and_evaluate` has run, and `run_agent_recovery` raises if the planner
was never invoked. Equivalent guarantee, different mechanism, but it is a
deviation from what was asked.

**`ToolCallTrace.model_dump` is hand-rolled** to mimic the Pydantic method the
API expects. The dataclass should just be a Pydantic model.
