# PROMPT 06 — Deterministic Constraint Validator

## Context

PRI architecture law: **deterministic software computes ALL numbers**.
The constraint validator is the module that proves an AI-proposed plan is wrong.
Gemini may *narrate* a violation using the `observed` / `required` strings it
reads from `ConstraintViolation`, but it never decides whether a violation exists.

## Task

Build `src/pri/engine/constraints/validator.py`.

## Policy file

Read all thresholds from `config/policies.yaml` (loaded once via `lru_cache`,
injectable in tests):

```yaml
crew_turnaround:
  minimum_hours: 10.0

shooting:
  maximum_daily_hours: 12.0

location:
  no_double_booking: true
  respect_permit_windows: true

cast:
  unavailable_dates_are_hard: true

equipment:
  no_double_booking: true
  respect_rental_windows: true

dependencies:
  preserve_prerequisite_order: true

scene_environment:
  enforce_int_ext_match: true
  enforce_time_of_day_match: true

approval:
  schedule_change:
    required: true
```

## API

```python
validate(state: ProductionState, *, policy: Policy | None = None) -> list[ConstraintViolation]
```

`Policy = dict[str, Any]` — a plain dict parsed from YAML.

## Rule architecture

Each rule is a **standalone function** with signature:

```python
def rule_name(state: ProductionState, policy: Policy) -> list[ConstraintViolation]:
    ...
```

Rules are registered in a module-level `RULES: list[RuleFunc]` list.
`validate()` calls every rule and concatenates the results.

This architecture means:
- Each rule is independently unit-testable
- The UI can display the rule code and human-readable failure strings
- Adding a rule is a one-line registration, not a rewrite

## The 10 rules

| Code | Name | Invariant |
|------|------|-----------|
| C001 | `crew_turnaround` | `wrap[day n] → call[day n+1] ≥ minimum_hours` |
| C002 | `max_daily_hours` | `wrap − call ≤ maximum_daily_hours` |
| C003 | `cast_availability` | No scene inside a cast member's `unavailable_window` |
| C004 | `location_permit` | Day's `[call, wrap)` fits inside the location's `permit_windows` |
| C005 | `location_double_book` | Two units at one location on the same calendar day |
| C006 | `equipment_window` | Equipment used outside its `available_windows` |
| C007 | `equipment_double_book` | Same equipment on two units on the same calendar day |
| C008 | `prerequisite_order` | Prerequisite scene scheduled on a strictly earlier date |
| C009 | `int_ext_match` | Scene `int_ext` is in `location.supports_int_ext` |
| C010 | `time_of_day_match` | Scene `time_of_day` is in `location.supports_time_of_day` |

**C001 detail**: days are ordered chronologically; for each consecutive pair
`(day_n, day_n+1)`, check `call[n+1] - wrap[n] >= timedelta(hours=minimum_hours)`.
Unit: exact `timedelta` arithmetic. Display as e.g. `"9.0h"`.

**C004 detail**: a day satisfies the permit constraint iff
`call_time >= window.start AND wrap_time <= window.end` for at least one permit
window. If `permit_windows` is empty, the constraint is trivially satisfied.

**C008 detail**: for each scene S with prerequisite P, find their scheduled dates.
Violation iff `date_of_S <= date_of_P` (prerequisite must be on a *strictly
earlier* date, not the same day).

## Violation string format

Every `ConstraintViolation` carries human-readable `observed` and `required`
strings. These are printed verbatim by the UI and read aloud in the demo.
**Make them good sentences.** Examples:

```
observed="9.0h"   required=">= 10.0h"
observed="sc-3 on 2025-09-10, sc-1 also on 2025-09-10"  required="sc-1 on earlier date"
```

## Internal helpers

Two distinct helpers prevent confusion:

- `_windows_for(windows, day)` → `bool`
  Returns `True` if `day.call_time / wrap_time` **overlaps** any window.
  Used by C006 (equipment outside window).

- `_day_fits_in_windows(windows, day)` → `bool`
  Returns `True` if the day is **fully contained** in at least one window.
  Used by C004 (permit window).

Both helpers return `True` when `windows` is empty (no restriction imposed).

## Tests

One focused test class per rule.  Each class constructs the minimal violating
`ProductionState` from scratch (no fixture dependency — rules are pure functions).

Key scenario test (one class, not just one method):

```python
class TestSwapScenario:
    """SwapDays(Sep 10, Sep 11) on the demo fixture produces exactly one C001."""
    def test_exactly_one_c001(self, night_train_state): ...
    def test_observed_value_is_nine_hours(self, night_train_state): ...
```

This test proves the numbers are real: the validator *observed* 9.0h, the policy
*required* ≥ 10.0h, and Gemini will read those exact strings aloud on screen.

## Acceptance criteria

- All 10 rule classes present and passing
- The swap scenario test passes with `observed="9.0h"`
- `validate()` is a pure function: no IO, no side-effects, no global state
- `ruff` and `mypy --strict` clean on this module

## Hard constraints

- No LLM call anywhere in this module
- No floating-point tolerance games — use exact `timedelta` arithmetic
- No `Any` in domain code; `Policy = dict[str, Any]` is the boundary type
- Docstrings on all public functions: inputs, outputs, failure modes
