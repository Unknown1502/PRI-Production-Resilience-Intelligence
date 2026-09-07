# Artifact 19 — Critical-Path Tests & CI

**Prompt:** [PROMPT_19_critical_path_tests.md](../prompts/PROMPT_19_critical_path_tests.md)
**Gate:** `SCORE`
**Outcome:** complete — 283 tests, three CI jobs

---

## Plan

Lock the demo path. Do not chase coverage; chase the demo.

## Files created

| File | Contents |
|---|---|
| [`tests/api/test_wow_scenario.py`](../../../tests/api/test_wow_scenario.py) | 26 tests — the full narrative over HTTP |
| `tests/api/conftest.py` | Clean schema, seeded fixture, in-loop ASGI client |
| [`tests/engine/test_transition.py`](../../../tests/engine/test_transition.py) | 16 tests — guards, idempotency, rollback |
| [`tests/agent/test_compliance.py`](../../../tests/agent/test_compliance.py) | 9 tests — forbidden SDKs, containment |
| [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml) | `backend`, `frontend`, `compliance` |

## The narrative, as one file

`test_wow_scenario.py` walks the path a judge walks:

```
schedule at v1, 9 days, Sep 10 = LOC-04 with S17/S18/S21
seeded state has zero HARD violations
graph is all "ok"
   ↓ POST /events  (LOC-04 blocked)
impact: S17 S18 S21 direct, S28 downstream
graph recolours: impacted + downstream present
   ↓ POST /recover
candidates A B C B2, mode "deterministic"
Plan B invalid: C001, observed 9.0h, required >= 10.0h
round-one note contains "3 candidates generated" and the rule
frontier == [plan-A, plan-B2]
explanation quotes C001, 9.0h and >= 10.0h
   ↓ execute without approval  → 409 at request_approval
   ↓ execute as someone else   → 409 at request_approval
   ↓ approve, then execute
seven steps completed in order
v1 → v2, six checks green
call sheets regenerated; the PDF downloads and starts with %PDF-
audit log contains every gate
executing twice → replayed, still v2
Sep 11 now calls at 08:30
```

## Test totals

| Suite | Count | Needs Postgres |
|---|---|---|
| `tests/domain` | 56 | |
| `tests/engine/constraints` | 44 | |
| `tests/engine/graph` | 41 | |
| `tests/engine/simulation` | 25 | |
| `tests/agent` | 24 | |
| `tests/api` | 26 | ✓ |
| `tests/persistence` | 24 | ✓ |
| `tests/engine/test_transition.py` | 16 | ✓ |
| `tests/engine/verification` | 11 | |
| `tests/engine/test_demo_fixture.py` | 9 | |
| `tests/events` | 7 | |
| **Total** | **283** | 66 |

## Decisions

**Real PostgreSQL in CI, as a service container.** The persistence, transition
and API suites test transactional guarantees. A fake repository would happily
"roll back" a write it never made.

**Integration-marked tests are skipped, not failed.** Anything needing a live
Confluent cluster or Vertex credentials is `@pytest.mark.integration`, and CI
runs `-m "not integration"`.

**A separate CI step re-runs the scenario suite verbosely.** So a reviewer can
see the six non-negotiable assertions pass by name without reading the whole log.

**A third `compliance` job.** Greps for forbidden AI SDKs across `src/` and the
frontend, and asserts the LICENSE is stock Apache-2.0. Cheaper to enforce in CI
than to discover during judging.

## Corrections made during the build

**The API tests were written with `TestClient` and did not work.** asyncpg binds
connections to the event loop that opened them; `TestClient` drives the app from
its own portal thread, so a pool created in the test's loop is unusable inside a
request. Every test errored with `another operation is in progress`, which reads
like a database fault and is not. Rewritten with `httpx.AsyncClient` over
`ASGITransport`, keeping everything on one loop.

## How to run it

```bash
docker compose -f docker-compose.dev.yml up -d
make test-fast                                    # excludes integration
pytest tests/engine/simulation/test_recovery_scenario.py -v
```

## Acceptance criteria

- ✅ The full narrative as an end-to-end test
- ✅ Unit tests per constraint rule, per Move kind, per scoring component
- ✅ Integration tests for Postgres
- ✅ CI: ruff, mypy strict, pytest with a Postgres service container
- ✅ Cloud-credential tests skipped, not failed
- 🔲 **Green CI badge on the public repo** — no repository exists yet

## Not covered

**CI has never run.** The workflow is written against the same commands used
locally, all of which pass, but GitHub Actions has not executed it once. The
likely first failures are the `sed` calls that rewrite `.env` (they assume
`.env.example` line formats) and the librdkafka apt install.

**No frontend tests beyond `tsc`, ESLint and a build.** The prompt's focus was
the demo path, and the demo path is asserted over HTTP — but nothing clicks a
button. A Playwright run driving the flow would be the highest-value addition to
the whole suite.

**No coverage measurement.** Deliberate, per the prompt, but it does mean nobody
knows which branches of the five event-type impact resolvers are exercised.
