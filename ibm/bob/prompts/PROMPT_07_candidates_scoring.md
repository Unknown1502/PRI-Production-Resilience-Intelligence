# PROMPT 07 — Candidate Generation, Scoring, Pareto & Replanning Loop

> This is the core algorithmic session. Record it. When a plan fails validation
> and the loop generates a repair, that is the moment the product earns its name.

## Context

PROMPT 06 built the constraint validator. PROMPT 07 builds everything that uses
it to generate, evaluate, repair and rank recovery options.

**Architecture law**: Gemini's `strategy_hints` only selects which families to
expand and in what order. It never creates a plan. Every number in every
`PlanScore` is computed here, never by the LLM.

## Deliverables

Four modules inside `src/pri/engine/simulation/`:

```
candidates.py   — enumerate recovery plans from strategy families
scoring.py      — deterministic PlanScore computation
pareto.py       — Pareto frontier tagging
replan.py       — the two-round loop with repair
```

Plus `config/scoring.yaml` — all weights and rates live there, **not in code**.

---

## `candidates.py`

```python
generate_candidates(
    state: ProductionState,
    impact: ImpactReport,
    strategy_hints: Sequence[str] | None = None,
    *,
    scoring_config: dict | None = None,
) -> list[CandidatePlan]
```

### Strategy families (exactly these four, in this order unless hints override)

| Family | What it generates |
|--------|-------------------|
| `DEFER` | `MoveSceneToDay(scene, next available reserve or unaffected day)` |
| `SWAP` | `SwapDays(impacted_day, compatible day within ±3 days)` |
| `RELOCATE` | `RelocateScene(scene, alt_location)` where alt supports scene's `int_ext` + `time_of_day` |
| `COMPRESS` | `ShiftCallTime` + partial `MoveSceneToDay` of overflowing scenes |

`strategy_hints` is a `Sequence[str]` from Gemini — it filters to those families
and sets the expansion order. If `None` or empty, all four families run.

### Constraints on generation

- Generate **at most `MAX_RAW_CANDIDATES = 8`** total before deduplication.
- Deduplicate by the SHA-256 `content_digest` of the resulting state
  (use `persistence.serialization.content_digest`). Two plans that produce
  identical schedules are the same plan.
- Plan labels: `"A"`, `"B"`, `"C"`, … assigned after deduplication.
- SWAP: only swap with days where `abs(date_a - date_b) <= SWAP_SEARCH_WINDOW_DAYS`.
- RELOCATE: only suggest locations that `_location_supports_all(location, scenes)`.

### Helper: `compress_day`

```python
compress_day(
    state, day_date, scenes_by_id, setup_minutes, *, new_call_time
) -> tuple[Move, ...]
```

Fits as many scenes as possible into the day starting at `new_call_time`, given
`setup_minutes` overhead per scene and `maximum_daily_hours` from policy.
Returns a sequence of `MoveSceneToDay` moves for scenes that overflow.
Used by both `_family_compress` and the round-2 repair in `replan.py`.

### `load_scoring_config(path=None) -> dict`

Loads `config/scoring.yaml` and caches with `lru_cache`.
Also used by `scoring.py` — one loader, two consumers.

---

## `scoring.py`

```python
score(
    base_state: ProductionState,
    candidate_state: ProductionState,
    *,
    config: dict | None = None,
) -> PlanScore
```

### The six dimensions

| Field | Definition |
|-------|-----------|
| `schedule_delay_days` | Duration-weighted mean slip of moved scenes. VFX plates multiplied by `delay.vfx_plate_slip_multiplier`. A day that was only reshaped (no scene moved) contributes `delay.call_time_shift_day_equivalent`. |
| `incremental_cost` | `Decimal`. Location days newly charged − released (unless `cost.forfeit_released_location`). Cast held on struck days × `cost.cast_holding_rate_fraction`. Equipment days added. Crew overtime × `cost.overtime_rate_per_hour`. Reserve day activations × `cost.reserve_day_activation_fee`. VFX plate relocations × `cost.vfx_plate_relocation_penalty`. |
| `operational_risk` | Weighted blend of five factors: `night_moves`, `ext_weather_exposure`, `reserve_days_consumed`, `cast_holding`, `vfx_plate_relocation`. Weights in `risk.weights`, saturation in `risk.saturation`. Must sum-check: raise `ScoringError` if weights don't sum to 1.0. Clamped to `[0, 1]`. |
| `affected_scene_count` | Scenes whose date **or** location changed. |
| `crew_disruption_hours` | Sum of `|call_time_shift|` and `|day_duration_change|` over all days. |
| `downstream_dependency_impact` | Moved scenes that are prerequisites of other scenes. |

### `config/scoring.yaml` (write this file)

```yaml
delay:
  measure: duration_weighted_mean_slip   # or max_scene_slip or mean_scene_slip
  vfx_plate_slip_multiplier: 2.0
  vfx_plate_relocation_slip_days: 0.5
  call_time_shift_day_equivalent: 0.1

cost:
  cast_holding_rate_fraction: "0.5"
  forfeit_released_location: true
  overtime_rate_per_hour: "210"
  reserve_day_activation_fee: "0"
  vfx_plate_relocation_penalty: "5000"

risk:
  weights:
    night_moves:           0.20
    ext_weather_exposure:  0.30
    reserve_days_consumed: 0.25
    cast_holding:          0.15
    vfx_plate_relocation:  0.10
  saturation:
    night_moves:           3.0
    ext_weather_exposure:  3.0
    reserve_days_consumed: 2.0
    cast_holding:          4.0
    vfx_plate_relocation:  2.0

capacity:
  setup_minutes_per_scene: 15
  maximum_daily_hours: 12.0

repair:
  turnaround_buffer_minutes: 30
```

**No numeric literal may appear in scoring arithmetic.** All values read from
`config`.

---

## `pareto.py`

```python
dominates(a: PlanScore, b: PlanScore) -> bool
mark_pareto(evaluated: Sequence[EvaluatedPlan]) -> list[EvaluatedPlan]
```

Minimised objectives: `(schedule_delay_days, incremental_cost, operational_risk)`.

Convert `float` objectives to `Decimal` via `str` before comparing — avoids
binary-float surprises where two plans are meant to tie.

Only valid, scored plans compete. Invalid plans are returned unchanged with
`pareto_optimal=False`.

---

## `replan.py`

```python
recover(
    state: ProductionState,
    event: DisruptionEvent,
    strategy_hints: Sequence[str] | None = None,
    max_rounds: int = 2,
    *,
    session_id: str | None = None,
    policy: Policy | None = None,
    scoring_config: dict | None = None,
) -> RecoveryResult
```

### Domain models to add to `models.py`

```python
RoundTrace(
    round_number: int,
    strategy_hints: tuple[str, ...],
    generated_plan_ids: tuple[str, ...],
    invalid_plan_ids: tuple[str, ...],
    failed_rule_codes: tuple[str, ...],
    repaired_plan_ids: tuple[str, ...],
    note: str,
)

RecoveryResult(
    session_id: str,
    production_id: str,
    event_id: str,
    base_version: int,
    impact: ImpactReport,
    evaluated: tuple[EvaluatedPlan, ...],
    rounds: tuple[RoundTrace, ...],
)
```

### The two-round loop

**Round 1** — generate, apply, validate, score all candidates.

**Round 2** — for each invalid candidate, call `build_repair`:
- Currently repairs `C001` (crew turnaround):
  read `violation.subject_ids` to find the late day, compute
  `new_call = early_day.wrap_time + timedelta(hours=minimum_hours) + timedelta(minutes=buffer)`,
  then call `compress_day` to fit the workload.
- Label the repair `"<original_label>2"` (Plan B → Plan B2).
- A failed plan that can't be repaired stays in the result as evidence.

Apply `mark_pareto` to the complete set (rounds 1 + 2) at the end.

### `evaluate_plan`

```python
evaluate_plan(state, plan, *, policy=None, scoring_config=None) -> EvaluatedPlan
```

Apply → validate → score. **Score even invalid plans** — a producer comparing
options needs to know what the rejected one would have cost.

---

## Tests — `tests/engine/simulation/test_recovery_scenario.py`

The full fixture scenario, deterministically:

```python
class TestRecoveryScenario:
    def test_plan_b_invalid_with_c001(...)
    def test_plan_b_observed_is_nine_hours(...)
    def test_plan_b2_is_valid(...)
    def test_pareto_set_is_exactly_a_and_b2(...)
    def test_result_is_deterministic_on_repeated_runs(...)
    def test_round_trace_records_c001(...)
```

These six tests are non-negotiable. They are both tests and demo evidence.

---

## Acceptance criteria

- All six scenario tests pass deterministically
- `ruff` + `mypy --strict` clean on all four modules
- No numeric literal in scoring arithmetic — all from `config/scoring.yaml`
- No LLM call anywhere in this module

## Hard constraints

- `strategy_hints` arrives as a plain `list[str]`. It only selects families —
  it never creates plan content.
- `score()` must use `Decimal` for all monetary arithmetic throughout.
- Fail loud: `ScoringError` if weights don't sum to 1.0.
