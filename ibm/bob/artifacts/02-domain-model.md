# Artifact 02 — Typed Domain Model

**Prompt:** [PROMPT_02_domain_model.md](../prompts/PROMPT_02_domain_model.md)
**Gate:** `SCORE`
**Outcome:** complete — 56 tests

---

## Plan

Build the pure value-object layer: every model frozen, every field typed, and a
single mechanical way to evolve state. No database, no IO, no LLM — this module
gets imported by everything, so it must depend on nothing but Pydantic.

## Files created

| File | Lines | Contents |
|---|---|---|
| [`src/pri/domain/models.py`](../../../src/pri/domain/models.py) | 927 | 20 frozen models, the `Move` union, `ProductionState.apply()` |
| `tests/domain/test_models.py` | 56 tests | Immutability, apply() purity, version chain, each Move kind |

Models: `TimeWindow`, `Production`, `Scene`, `Person`, `Location`, `Equipment`,
`ShootingDay`, `Schedule`, `ProductionState`, `DisruptionEvent`, `CandidatePlan`,
`ConstraintViolation`, `PlanScore`, `EvaluatedPlan`, `ImpactReport`, plus the
four `Move` variants and (added in session 07) `RoundTrace` and `RecoveryResult`.

## Decisions

**Tuples, not lists, throughout.** `frozen=True` stops attribute assignment but
does nothing about a mutable list inside a "frozen" model. Tuples make the
immutability real.

**`apply()` never validates.** It applies moves mechanically and returns a new
state. Validation is session 06's job with its own test suite. Merging them
would mean every candidate generation had to reason about partial failure.

**Half-open time windows `[start, end)`.** Stated once in `TimeWindow` and
relied on by every rule that compares a day to a window. The alternative —
each rule deciding for itself — is how off-by-one-hour bugs get in.

## Corrections made during the build

Two defects surfaced only when the real fixture existed in session 05, and both
were blocking the demo arithmetic:

**`SwapDays` exchanged only the scene lists.** A day swap in production moves
the whole day — location, call, wrap and scenes. Swapping scenes alone stranded
them at a location that could not host them, and left the original turnaround
intact, which is precisely the constraint the swap is meant to stress. Now
swaps full day content and re-anchors the clock times onto the new date via
`_rebase_day`. **This is what produces the 9.0h C001 violation.**

**`ShiftCallTime` moved the wrap along with the call.** That preserved the day's
length by pushing the wrap past LOC-04's 19:00 permit — trading a turnaround
violation for a permit violation. Wrap now holds; the day gets shorter and the
generator sheds scenes to fit.

One addition: `apply()` now re-points a day at its scenes' location when they
unanimously agree (`_derive_day_locations`), so a RELOCATE plan actually moves
the unit rather than leaving it booked at a blocked address.

## How to run it

```bash
pytest tests/domain -q
mypy src/pri/domain
```

## Acceptance criteria

- ✅ mypy strict passes on `src/pri/domain`
- ✅ Immutability enforced — assignment raises `ValidationError`
- ✅ `apply()` never mutates its input
- ✅ Version chain correct: `version + 1`, `parent_version` set
- ✅ Each Move kind applies correctly
- ✅ No database, IO or LLM code in the module

## Not covered

`apply()` raises `KeyError` for a move referencing a date that is not a shooting
day. That is correct, but it means the candidate generator has to know which
dates are valid targets before it builds a move — the domain offers no "can this
move be applied?" predicate, so `candidates.py` catches the exception instead.
Workable; not elegant.
