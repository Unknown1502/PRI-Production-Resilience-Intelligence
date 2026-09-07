# PROMPT 19 — Critical-Path Tests

## Task

Lock the demo path with tests. **Do not chase coverage; chase the demo.**

## `tests/e2e/test_wow_scenario.py`

The full narrative as one test:

1. seed
2. publish **LOC-04 blocked**
3. impact matches expected sets
4. candidates generated
5. **Plan B invalid with C001, observed 9.0h**
6. **Plan B2 generated and valid**
7. Pareto set == `{A, B2}`
8. approve B2
9. execute
10. six verification checks pass
11. `version == 2`
12. call sheet regenerated with the new call time

## Plus

| Suite | Contents |
|---|---|
| `tests/unit/` | one per constraint rule, per Move kind, per scoring component |
| `tests/integration/` | Postgres, Confluent round-trip, Gemini call (marked, skippable) |

## CI

Add `.github/workflows/ci.yml`: ruff, mypy strict on domain+engine, pytest with a
Postgres service container. Integration tests requiring cloud credentials are
**skipped in CI, not failed**.

## Acceptance Criteria

Green CI badge on the public repo **before you record the video**.
