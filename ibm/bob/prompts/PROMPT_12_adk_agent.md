# PROMPT 12 — Google ADK Agent + Gemini

> `[GATE]` — record this session in full for the demo video.

## Task

Build `src/pri/agent/` using **native `google-adk` ONLY**.

Verify the current `google-adk` API against the **installed package** before
writing code — read the installed package's own source/docs and follow it exactly.
Do not write from memory and do not invent signatures. Pin the resolved version in
`pyproject.toml`. Read the Gemini model id from settings (`GOOGLE_GENAI_MODEL`);
**do not hardcode a model name**.

## `agent/tools.py`

Typed `FunctionTool`s, each a thin wrapper over the deterministic engine
(**in-process calls, not HTTP**):

| Tool | Returns |
|---|---|
| `get_production_state(production_id)` | compact state summary |
| `get_impact(production_id, event_id)` | `ImpactReport` as dict |
| `get_constraints()` | the active policy document |
| `generate_and_evaluate(production_id, event_id, strategy_hints)` | `RecoveryResult` as dict (calls `engine.simulation.replan.recover`) |
| `get_plan_comparison(session_id)` | plans with scores, validity, violations, and pareto flags |
| `request_approval(session_id, plan_id, justification)` | queues for the human |

## `agent/root_agent.py`

A **single** ADK root agent (NOT five sub-agents; more agents is not more
intelligence). Its instruction lives in `agent/prompts/root.md` and must state:

> You do not compute numbers. You interpret a production disruption, choose
> which recovery strategy families to explore (DEFER, SWAP, RELOCATE, COMPRESS),
> call `generate_and_evaluate`, then explain the deterministic results to the
> producer in plain language: what broke, what the options cost, why an option
> was rejected and by which rule, and which Pareto-optimal option you recommend
> and why. Quote only numbers returned by tools. If a tool returns a constraint
> violation, state the rule code and the observed vs required values verbatim.
> Never claim you discovered your own mistake — say the candidate failed
> deterministic validation and the planner generated a repair.

## `agent/service.py`

```python
run_recovery(production_id, event_id) -> AgentRecoveryOutput
# { session_id, strategy_hints_used, recommended_plan_id, explanation,
#   tradeoff_summary, tool_calls: list[ToolCallTrace] }
```

`ToolCallTrace` is surfaced in the UI — showing **real** tool calls is a scoring asset.

## Wiring

Wire into `POST /api/productions/{id}/recover` behind a settings flag
`PRI_AGENT_ENABLED`, with the **pure-deterministic path as fallback** so the demo
never dies on a quota error.

## Tests

- Mock the model
- Assert the agent calls `generate_and_evaluate` **before** producing an explanation
- Assert **no numeric value in the explanation is absent from the tool output**

## Acceptance Criteria

A real Gemini call runs end to end against the fixture and the explanation names
the rule that Plan B failed, correctly.

## Constraints

- DO NOT import `langchain`, `langgraph`, `openai`, `anthropic`, or any non-Google AI SDK.
