# Artifact 06 — Deterministic Constraint Validator

**Prompt:** [PROMPT_06_constraint_validator.md](../prompts/PROMPT_06_constraint_validator.md)
**Gate:** `SCORE` · 🎥 **session recorded in full**
**Outcome:** complete — 44 tests, 10 rules

---

## Plan

This is the module that proves an AI-proposed plan is wrong. Ten rules, each a
standalone function in a registry, each producing a violation that carries what
it *observed* and what it *required* in strings a person can read aloud.

## Files created

| File | Lines | Contents |
|---|---|---|
| [`engine/constraints/validator.py`](../../../src/pri/engine/constraints/validator.py) | 781 | 10 rules, `RULES`, `RULE_CODES`, `validate`, `load_policy` |
| `config/policies.yaml` | — | Every threshold |
| `tests/engine/constraints/test_validator.py` | 44 tests | One class per rule + `TestSwapScenario` |

## The ten rules

| Code | Name | Invariant |
|---|---|---|
| C001 | `crew_turnaround` | `call[n+1] − wrap[n] ≥ minimum_hours` |
| C002 | `max_daily_hours` | `wrap − call ≤ maximum_daily_hours` |
| C003 | `cast_availability` | No scene inside a cast unavailable window |
| C004 | `location_permit` | The day fits **inside** a permit window |
| C005 | `location_double_book` | Two units, one location, one day |
| C006 | `equipment_window` | Equipment used inside its rental window |
| C007 | `equipment_double_book` | Same equipment, two units, one day |
| C008 | `prerequisite_order` | Prerequisite on a **strictly earlier date** |
| C009 | `int_ext_match` | Scene INT/EXT supported by its location |
| C010 | `time_of_day_match` | Scene time of day supported by its location |

## Decisions

**The violation strings were written before the rules.** `observed="9.0h"`,
`required=">= 10.0h"` came first, and every rule then had to be able to say what
it saw and what it wanted in one line. That constraint shaped the whole module,
and it is why the UI can print them verbatim and the narrator can read them.

**Two helpers, deliberately distinct.** `_windows_for` asks *overlap* — is this
resource touched at all today? `_day_fits_in_windows` asks *containment* — does
the whole day sit inside the permit? A permit that expires at 19:00 is not
satisfied by a day that merely starts before then. Both return `True` on an
empty window list, meaning "no restriction".

**Exact `timedelta` and `Decimal`. No tolerance.** A turnaround of 9 hours 59
minutes is a violation. Introducing an epsilon would mean the demo's 9.0h could
have been 9.4h depending on a constant nobody reads.

**`validate()` is pure.** No IO, no clock, no global state; the policy is
injectable. Same input, same verdict, forever — which is what lets session 08
re-validate at execution time and trust the answer.

## The C008 tightening, and what it cost

The rule now requires a prerequisite on a **strictly earlier date**. Two scenes
in the same dependency chain cannot share a day, however the shot list is
ordered — because the running order changes on the morning, and a dependency
that survives only through slug order is not one a production can rely on.

Enforcing it broke two things that were previously passing, and both were real:

1. **The seeded fixture became illegal.** Appendix A had two same-day
   prerequisite edges. Fixed in the fixture, not by weakening the rule.
2. **The DEFER family produced an invalid plan.** It was putting a dependant on
   the same reserve day as its prerequisite. Fixed in session 07.

An earlier draft of this rule compared `(date, index within day)`, which let
same-day pairs pass. That was overturned when the specification was tightened —
the correct call, and the tests are what made the consequences visible rather
than latent.

## How to run it

```bash
pytest tests/engine/constraints -q
```

## Acceptance criteria

- ✅ All 10 rule classes present and passing
- ✅ `TestSwapScenario` passes with `observed="9.0h"` against the real fixture
- ✅ `validate()` pure — no IO, no side-effects, no global state
- ✅ ruff and `mypy --strict` clean
- ✅ Exact arithmetic; no floating-point tolerance
- ✅ No LLM call

## Not covered

**No SOFT violations are ever emitted.** Every rule returns `HARD`. The model
supports a soft severity and the UI renders it, but nothing produces one — a
"you could do this but it will hurt" tier would be genuinely useful and does
not exist.

**Nothing checks that a day's work fits its hours.** Scene minutes versus the
call-to-wrap window is handled by the candidate generator's capacity model, not
by a rule. So a state can be schedule-legal and physically unshootable, and
`validate()` will pass it.
