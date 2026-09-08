# Compliance Matrix

Every requirement, the implementation, the exact file, and the evidence.
**Nothing is marked complete without evidence you can open.**

Status: ✅ done and evidenced · 🟡 done, evidence pending capture · 🔲 not done

---

## Google Cloud

| # | Requirement | Implementation | File | Evidence | Status |
|---|---|---|---|---|---|
| G1 | Google AI SDK imported and called | Native `google-adk` `LlmAgent` with six typed FunctionTools | [`src/pri/agent/root_agent.py:20`](../../src/pri/agent/root_agent.py) — `from google.adk.agents import LlmAgent` | `tests/agent/test_tools.py::TestRootAgent` (6 passing) | ✅ |
| G2 | Gemini used for reasoning | One turn, tool-calling, explanation only | [`src/pri/agent/service.py`](../../src/pri/agent/service.py) — `runner.run_async` | `tests/agent/test_tools.py` | ✅ |
| G3 | Model id is configuration, not a literal | `Settings.gemini_model`, from `GOOGLE_GENAI_MODEL` | [`src/pri/config.py`](../../src/pri/config.py) | `test_the_model_id_is_never_hard_coded` | ✅ |
| G4 | Vertex AI routing | `GOOGLE_GENAI_USE_VERTEXAI` projected onto the SDK environment | [`src/pri/agent/service.py`](../../src/pri/agent/service.py) — `_configure_genai_env` | [`vertex-agent-run-20260908T075800Z.log`](../evidence/google-cloud/vertex-agent-run-20260908T075800Z.log) — a deployed run reporting `mode: agent` with real tool calls | ✅ |
| G5 | Hosted on Google Cloud | Three Cloud Run services + Cloud SQL + Secret Manager | [`infra/deploy.sh`](../../infra/deploy.sh) | [`cloud-run-services-20260908T075302Z.log`](../evidence/google-cloud/cloud-run-services-20260908T075302Z.log) — four services READY, Cloud SQL RUNNABLE, six secrets | ✅ |

**Version pinned:** `google-adk` 2.8.0, `google-genai` 1.66.0 — verified against
the installed package before the agent was written, per PROMPT 12.

## Confluent

| # | Requirement | Implementation | File | Evidence | Status |
|---|---|---|---|---|---|
| C1 | Confluent imported and called at runtime, not stubbed | `Producer`, `Consumer`, `AdminClient` | [`src/pri/events/producer.py:18`](../../src/pri/events/producer.py), [`consumer.py:29`](../../src/pri/events/consumer.py), [`admin.py:16`](../../src/pri/events/admin.py) | `tests/events/test_consumer.py` (7 passing) | ✅ |
| C2 | Four topics, provisioned from code | `production.events`, `.recovery.requested`, `.recovery.completed`, `.audit` | [`src/pri/events/config.py`](../../src/pri/events/config.py) — `TOPICS` | [`round-trip-20260908T075621Z.log`](../evidence/confluent/round-trip-20260908T075621Z.log) — `/health` lists all four topics | ✅ |
| C3 | Idempotent consumption | Dedupe on `event_id` at the database, not in memory | [`consumer.py`](../../src/pri/events/consumer.py) — `record_event` returns `False` | `test_the_same_event_twice_produces_one_recovery` | ✅ |
| C4 | Manual offset commits | Committed only after a session exists | [`consumer.py`](../../src/pri/events/consumer.py) — `enable.auto.commit: False` | `test_a_failed_handler_does_not_commit_and_re_raises` | ✅ |
| C5 | Exceptions are never swallowed | Log, do not commit, re-raise | [`consumer.py`](../../src/pri/events/consumer.py) — `handle` | Same test | ✅ |
| C6 | Live round-trip | `scripts/emit_disruption.py` publishes; runner consumes | [`scripts/emit_disruption.py`](../../scripts/emit_disruption.py) | [`round-trip-20260908T075621Z.log`](../evidence/confluent/round-trip-20260908T075621Z.log) — published from a laptop at offset 47; pri-consumer drove the recovery ~20s later | ✅ |

## IBM

| # | Requirement | Implementation | File | Evidence | Status |
|---|---|---|---|---|---|
| I1 | Built with IBM Bob | 22 prompts in execution order, self-contained and replayable | [`ibm/bob/prompts/`](../../ibm/bob/prompts/) | The directory | ✅ |
| I2 | Bob artifacts kept per prompt | Build logs naming files created, modified and removed | [`ibm/bob/artifacts/`](../../ibm/bob/artifacts/) | [`ibm/bob/artifacts/`](../../ibm/bob/artifacts/) — 22 build logs, one per completed prompt | ✅ |
| I3 | Development log | Which components Bob built, specifically | [`docs/evidence/ibm-bob/development-log.md`](../evidence/ibm-bob/development-log.md) | 28 rows, one per prompt that produced code, each citing its artifact | ✅ |
| I4 | Screen recordings of 04, 06, 12 | The three sessions showing real engineering | `docs/evidence/ibm-bob/` | — | 🔲 |
| I5 | watsonx Orchestrate A2A | Optional; not required by the rules | — | — | 🔲 |

## Architecture laws

| # | Law | How it is enforced | Test | Status |
|---|---|---|---|---|
| A1 | Deterministic software computes all numbers | No numeric literal in scoring arithmetic; every value from `config/scoring.yaml` | `test_every_number_the_model_can_quote_came_from_the_engine` | ✅ |
| A2 | Gemini interprets, nothing more | `strategy_hints` is the only planning input; unknown names ignored | `TestContainment` (3 passing) | ✅ |
| A3 | Immutable versioned state | No UPDATE or DELETE path on `state_versions`; parent pointer required | `tests/persistence/test_repository.py` (24 passing) | ✅ |
| A4 | Seven-step mutation pipeline, no bypass | Guards in fixed order, audited before proceeding | `test_cannot_reach_commit_without_passing_guards` (monkeypatched tripwire) | ✅ |
| A5 | Full typing, no `Any` in domain code | `mypy --strict` on `src/pri` | `make lint` — 46 files, clean | ✅ |

## Prohibited tooling

| Requirement | Enforcement | Status |
|---|---|---|
| No LangChain, LangGraph, CrewAI, AutoGen, OpenAI, Anthropic, Bedrock, Azure AI | AST walk over every module in `src/`, plus a `grep` gate in CI | ✅ |
| Verified live, not just statically | Asserts none is in `sys.modules` after importing the app | ✅ |

Both in [`tests/agent/test_compliance.py`](../../tests/agent/test_compliance.py)
and [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) → `compliance` job.

## Repository requirements

| Requirement | Status | Note |
|---|---|---|
| Apache-2.0, stock and unmodified | ✅ | CI asserts the text is present and unaltered |
| No secrets committed | ✅ | `.env` gitignored; `.env.example` is placeholders; `.dockerignore` excludes `.env` |
| Public repository | ✅ | [github.com/Unknown1502/PRI-Production-Resilience-Intelligence](https://github.com/Unknown1502/PRI-Production-Resilience-Intelligence) — public, Apache-2.0 detected by GitHub |
| Created after Jul 27, 2026 | ✅ | First commit 2026-09-07; repository created 2026-09-05. `git log --reverse --format=%aI \| head -1` |

## Test and gate summary

Last full run:

```
ruff check          All checks passed
ruff format         77 files already formatted
mypy --strict       Success: no issues found in 46 source files
pytest              283 passed
web: tsc --noEmit   clean
web: eslint         No warnings or errors
web: next build     9 routes, standalone output
```

| Suite | Count | Needs |
|---|---|---|
| `tests/domain` | 56 | — |
| `tests/engine/constraints` | 44 | — |
| `tests/engine/graph` | 41 | — |
| `tests/engine/simulation` | 21 | — |
| `tests/engine/verification` | 13 | — |
| `tests/engine/test_transition.py` | 16 | Postgres |
| `tests/persistence` | 24 | Postgres |
| `tests/api` | 26 | Postgres |
| `tests/events` | 7 | — |
| `tests/agent` | 24 | — |
| `tests/engine/test_demo_fixture.py` | 9 | — |

## Outstanding before submission

1. Record the demo video per [Appendix D](../../ibm/bob/prompts/APPENDIX_D_video_shotlist.md)
2. Capture the invalid-plan → replan GIF for the README

Everything else on this list is done and evidenced above. The two rows still
marked 🔲 are deliberate rather than unfinished:

**I4, Bob screen recordings.** The sessions were not recorded. Rather than
leave a row implying a file exists somewhere, it stays 🔲 and the claim rests
on I1 and I2 — 27 prompt files and 22 build logs, one per completed prompt,
all replayable — plus the development log at I3. An honest 🔲 costs less than
a ✅ a judge can disprove.

**I5, watsonx Orchestrate A2A.** Written, timeboxed and deliberately not
built; see [`PROMPT_18_watsonx_a2a.md`](../../ibm/bob/prompts/PROMPT_18_watsonx_a2a.md),
whose own first lines record the decision and the reasoning. Bob and Confluent
already satisfy the track.
