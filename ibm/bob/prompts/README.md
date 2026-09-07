# PRI — Bob Prompt Archive

This directory contains the complete sequence of task prompts used to build
**Production Resilience Intelligence (PRI)** with IBM Bob.

Each file is numbered in execution order and is self-contained — it can be
replayed in a fresh Bob session to regenerate the corresponding module.

## How to use this pack

1. **Paste [PROMPT 00](PROMPT_00_system_rules.md) at the start of every new Bob
   session.** Bob loses the constitution when context resets; re-anchoring costs
   20 seconds and saves an hour of wrong-direction code.
2. **After each prompt, save the evidence:**
   - `ibm/bob/prompts/NN-slug.md` — the prompt you pasted
   - `ibm/bob/artifacts/NN-slug.md` — Bob's plan/summary + the files it touched
3. **Screen-record prompts 04, 06 and 12 in full.** Those are the three that show
   Bob doing real engineering (impact analysis, constraint logic, ADK agent).
4. **Commit after every prompt** with the message `bob(NN): <what it built>`.
   The commit history *is* the Bob evidence trail.
5. **If a prompt's acceptance criteria fail twice, cut the feature and move on.**
   See [Appendix C](APPENDIX_C_cut_ladder.md).

## Prompt Index

| File | Day | Gate | What it builds |
|------|-----|------|----------------|
| [PROMPT_00_system_rules.md](PROMPT_00_system_rules.md) | — | — | Architecture laws, competition constraints, style rules |
| [PROMPT_01_repo_skeleton.md](PROMPT_01_repo_skeleton.md) | 1 | `GATE` | `pyproject.toml`, `.env.example`, package layout, `config.py`, Makefile |
| [PROMPT_02_domain_model.md](PROMPT_02_domain_model.md) | 1 | `SCORE` | `src/pri/domain/models.py` — Pydantic v2 frozen models + `ProductionState.apply()` |
| [PROMPT_03_persistence.md](PROMPT_03_persistence.md) | 1 | `SCORE` | `src/pri/persistence/` — SQL schema, async repo, `StaleStateError`, event idempotency |
| [PROMPT_04_graph_impact.md](PROMPT_04_graph_impact.md) | 1 | `SCORE` 🎥 | `engine/graph/dependency.py` — NetworkX graph, `impact_of()`, `graph_payload()` |
| [PROMPT_05_seed_fixture.md](PROMPT_05_seed_fixture.md) | 1 | `SCORE` | `data/fixtures/night_train.json` + `persistence/seed.py` |
| [PROMPT_06_constraint_validator.md](PROMPT_06_constraint_validator.md) | 1 | `SCORE` 🎥 | `engine/constraints/validator.py` — 10 deterministic rules |
| [PROMPT_07_candidates_scoring.md](PROMPT_07_candidates_scoring.md) | 1 | `SCORE` | `engine/simulation/` — candidates, scoring, Pareto, the replan loop |
| [PROMPT_08_verification_transition.md](PROMPT_08_verification_transition.md) | 1 | `SCORE` | `engine/verification/verify.py` (V1–V6) + `engine/transition.py` |
| [PROMPT_09_fastapi.md](PROMPT_09_fastapi.md) | 2 | `GATE` | `src/pri/api/` — routes + SSE recovery stream |
| [PROMPT_10_confluent_events.md](PROMPT_10_confluent_events.md) | 2 | `GATE` | `src/pri/events/` — Confluent producer, consumer, runner |
| [PROMPT_11_call_sheet.md](PROMPT_11_call_sheet.md) | 2 | `SCORE` | `src/pri/artifacts/call_sheet.py` — reportlab PDF |
| [PROMPT_12_adk_agent.md](PROMPT_12_adk_agent.md) | 2 | `GATE` 🎥 | `src/pri/agent/` — native google-adk root agent + tools |
| [PROMPT_13_frontend_scaffold.md](PROMPT_13_frontend_scaffold.md) | 3 | `SCORE` | `web/` — Next.js 15 shell, design system, overview |
| [PROMPT_14_impact_recovery_screens.md](PROMPT_14_impact_recovery_screens.md) | 3 | `SCORE` | Disruption + Recovery screens (the demo carriers) |
| [PROMPT_15_governance_screens.md](PROMPT_15_governance_screens.md) | 3 | `SCORE` | Governance, verification, audit screens |
| [PROMPT_16_cloud_run_deploy.md](PROMPT_16_cloud_run_deploy.md) | 3 | `GATE` | Dockerfiles + `infra/deploy.sh` to Cloud Run |
| [PROMPT_17_demo_runner.md](PROMPT_17_demo_runner.md) | 3 | `SCORE` | `scripts/run_demo.py`, reset endpoint, deterministic fallback |
| [PROMPT_18_watsonx_a2a.md](PROMPT_18_watsonx_a2a.md) | 3 | `OPTIONAL` | watsonx Orchestrate external agent over A2A |
| [PROMPT_19_critical_path_tests.md](PROMPT_19_critical_path_tests.md) | 4 | `SCORE` | `tests/e2e/test_wow_scenario.py` + CI |
| [PROMPT_20_readme_evidence.md](PROMPT_20_readme_evidence.md) | 4 | `GATE` | README, `docs/evidence/`, compliance matrix |
| [PROMPT_21_devpost_copy.md](PROMPT_21_devpost_copy.md) | 4 | `GATE` | `docs/hackathon/devpost.md` |

## Appendices

| File | Use |
|---|---|
| [APPENDIX_A_demo_fixture.md](APPENDIX_A_demo_fixture.md) | The demo production — paste into PROMPT 05 |
| [APPENDIX_B_guardrails.md](APPENDIX_B_guardrails.md) | Append when Bob starts drifting |
| [APPENDIX_C_cut_ladder.md](APPENDIX_C_cut_ladder.md) | What to cut, in order, when behind |
| [APPENDIX_D_video_shotlist.md](APPENDIX_D_video_shotlist.md) | The three-minute demo video |

## Build status

| Prompt | Module(s) built | State |
|---|---|---|
| 00 | — | ✅ Contract established |
| 01 | `pyproject.toml`, `.env.example`, `config.py`, `Makefile` | ✅ Complete |
| 02 | `src/pri/domain/models.py` | ✅ Complete — 56 tests |
| 03 | `src/pri/persistence/` | ✅ Complete — 24 integration tests (real Postgres) |
| 04 | `src/pri/engine/graph/dependency.py` | ✅ Complete — 41 tests |
| 05 | `data/fixtures/night_train.json`, `persistence/seed.py`, `bootstrap.py` | ✅ Complete |
| 06 | `src/pri/engine/constraints/validator.py` | ✅ Complete — 44 tests, 10 rules, `TestSwapScenario` |
| 07 | `src/pri/engine/simulation/` + `config/scoring.yaml` | ✅ Complete — all six scenario tests pass |
| 08 | `engine/verification/verify.py`, `engine/transition.py` | ✅ Complete — 16 transition tests incl. commit tripwire + rollback |
| 09 | `src/pri/api/` — routes, SSE, typed errors | ✅ Complete — 26 end-to-end tests over HTTP |
| 10 | `src/pri/events/` + `scripts/emit_disruption.py` | ✅ Complete — 7 tests; live round-trip pending a cluster |
| 11 | `src/pri/artifacts/call_sheet.py` + `config/logistics.yaml` | ✅ Complete |
| 12 | `src/pri/agent/` — root agent, tools, service | ✅ Complete — 24 tests; live Gemini call pending credentials |
| 13 | `web/` — Next.js 15 shell, design system, overview | ✅ Complete |
| 14 | `web/app/disruption`, `web/app/recovery` | ✅ Complete |
| 15 | `web/app/governance`, `verification`, `audit` | ✅ Complete |
| 16 | `Dockerfile.{api,web,consumer}`, `infra/deploy.sh`, `infra/README.md` | ✅ Written — not yet run against a project |
| 17 | `scripts/run_demo.py`, `/api/demo/reset`, deterministic fallback | ✅ Complete — three identical runs verified |
| 18 | watsonx Orchestrate A2A | 🔲 Optional, not started |
| 19 | `tests/api/test_wow_scenario.py`, `.github/workflows/ci.yml` | ✅ Complete |
| 20 | `README.md`, `docs/evidence/`, `docs/hackathon/compliance-matrix.md` | ✅ Complete — screenshots pending |
| 21 | `docs/hackathon/devpost.md` | ✅ Complete |

Gates as of the last run:

```
ruff check          All checks passed
ruff format         77 files already formatted
mypy --strict       Success: no issues found in 46 source files
pytest              283 passed  (Postgres required for 66 of them)
web: tsc --noEmit   clean
web: eslint         No warnings or errors
web: next build     9 routes, standalone output
```

The demo, end to end, in 1.3 seconds:

```
CANDIDATE_INVALID  Plan B — C001: observed 9.0h, required >= 10.0h
CANDIDATE_VALID    Plan B2 generated and valid
AWAITING_APPROVAL  recommending plan-B2, frontier ['plan-A', 'plan-B2']
VERIFIED           6/6 checks green, v1 -> v2
call sheet         Sep 11 now calls at 08:30 with ['S17', 'S18', 'S21']
```

## Architecture Laws (always active)

1. **Deterministic software computes ALL numbers** — Gemini never produces a number the system trusts.
2. **Gemini interprets and narrates** using numbers fed to it — nothing more.
3. **Immutable versioned state** — every change is a new version with a parent pointer.
4. **Full mutation pipeline** — `validate_state → validate_constraints → validate_policy → verify_authorization → request_approval → execute → verify_result`
5. **Full typing everywhere** — Pydantic v2 / TypeScript strict, no `Any` in domain code.

## Stack

| Layer | Technology |
|-------|-----------|
| AI | `google-adk` + Gemini (exclusively) |
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| Graph | NetworkX |
| State store | PostgreSQL (async SQLAlchemy 2 + asyncpg) |
| Event fabric | Confluent Kafka (`confluent-kafka`) |
| Frontend | Next.js 15 + TypeScript + Tailwind + `@xyflow/react` |

## Forbidden, no exceptions

LangChain, LangGraph, CrewAI, AutoGen, OpenAI, Anthropic, AWS Bedrock, Azure AI,
or any non-Google AI/agent SDK.
