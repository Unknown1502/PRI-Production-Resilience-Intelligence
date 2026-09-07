# Artifact 00 — System Rules & Architecture Laws

**Prompt:** [PROMPT_00_system_rules.md](../prompts/PROMPT_00_system_rules.md)
**Type:** session-opening contract, pasted at the top of every subsequent session
**Outcome:** acknowledged

---

## Rules loaded

Five architecture laws, read back and held for the rest of the build:

1. Deterministic software computes **all** numbers. Gemini never produces a
   number the system treats as true.
2. Gemini interprets events, proposes which strategy families to explore, and
   explains trade-offs using numbers supplied to it. That is all.
3. Never mutate committed state in place. Every change is a new immutable
   version with a parent pointer.
4. No consequential mutation bypasses
   `validate_state → validate_constraints → validate_policy →
   verify_authorization → request_approval → execute → verify_result`.
5. Every module typed. No `Any` in domain code.

Competition constraints: `google-adk` + Gemini only; LangChain, LangGraph,
CrewAI, AutoGen, OpenAI, Anthropic, Bedrock and Azure AI forbidden outright;
Confluent as the event fabric; PostgreSQL as the state store; no secrets in the
repository.

## How each law ended up enforced

A contract nobody checks is a comment. Each law has something that fails if it
is broken:

| Law | Enforcement | Where |
|---|---|---|
| 1 | No numeric literal in scoring arithmetic; every value read from `config/scoring.yaml` | [`simulation/scoring.py`](../../../src/pri/engine/simulation/scoring.py) |
| 1 | Tool payload re-checked against an independent planner run | `tests/agent/test_tools.py::test_every_number_the_model_can_quote_came_from_the_engine` |
| 2 | The agent's only planning input is `strategy_hints: list[str]`, matched against four constants | [`agent/tools.py`](../../../src/pri/agent/tools.py) |
| 3 | No UPDATE or DELETE path exists on `state_versions` | [`persistence/repository.py`](../../../src/pri/persistence/repository.py) |
| 4 | Commit tripwire: monkeypatch `commit_state`, assert it is never reached with a guard unsatisfied | `tests/engine/test_transition.py::test_cannot_reach_commit_without_passing_guards` |
| 5 | `mypy --strict` across 46 modules, in CI | [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml) |
| Forbidden SDKs | AST walk over every module, plus a live `sys.modules` check | `tests/agent/test_compliance.py` |

## Files created

None. This session establishes the contract only.

## Files modified

None.

## How to run it

Re-paste the prompt at the start of any new session before the real task. It
costs twenty seconds and prevents an hour of code built against the wrong
assumptions.

## Not covered

The contract says nothing about **what happens when two laws conflict**. That
came up for real in session 06: tightening rule C008 to satisfy law 1's spirit
made the seeded fixture illegal, which broke session 05's acceptance criterion.
Resolving it needed a human decision about which document was authoritative, and
that escalation path is not written down anywhere in the pack.
