# Artifact 07 — Candidates, Scoring, Pareto & the Replanning Loop

**Prompt:** [PROMPT_07_candidates_scoring.md](../prompts/PROMPT_07_candidates_scoring.md)
**Gate:** `SCORE`
**Outcome:** complete — 25 tests, all six required scenario tests passing

---

## Plan

Everything that uses the validator: enumerate recovery plans from four strategy
families, score them deterministically, mark the Pareto frontier, and run the
two-round loop that turns a rejection into a repair.

## Files created

| File | Lines | Contents |
|---|---|---|
| [`simulation/candidates.py`](../../../src/pri/engine/simulation/candidates.py) | 622 | Four families, `generate_candidates`, `compress_day` |
| [`simulation/scoring.py`](../../../src/pri/engine/simulation/scoring.py) | 390 | `score()` — six dimensions, `Decimal` money |
| [`simulation/pareto.py`](../../../src/pri/engine/simulation/pareto.py) | 90 | `dominates`, `mark_pareto` |
| [`simulation/replan.py`](../../../src/pri/engine/simulation/replan.py) | 385 | `recover()`, `evaluate_plan`, `build_repair` |
| [`simulation/narrate.py`](../../../src/pri/engine/simulation/narrate.py) | 153 | The deterministic-mode explanation |
| [`config/scoring.yaml`](../../../config/scoring.yaml) | — | Every rate, weight and saturation point |
| `tests/engine/simulation/test_recovery_scenario.py` | 25 tests | `TestRecoveryScenario` + four supporting classes |

`RoundTrace` and `RecoveryResult` were added to `domain/models.py`.

## Files removed

`simulation/planner.py` — an earlier pass using strategy families
(`SHIFT_DAY`, `SWAP_BLOCK`, `TRIM_DAY`) that the settled specification does not
have. Replaced rather than adapted; reconciling two contracts costs more than
rewriting one of them.

## Decisions

**No numeric literal in the scoring arithmetic.** Every rate, weight and
saturation point is read from `config/scoring.yaml`. A judge can audit the cost
model by reading one YAML file instead of tracing constants through Python.

**`ScoringError` if the risk weights do not sum to 1.0.** A silently
mis-weighted risk score is worse than no risk score.

**Invalid plans are scored anyway.** A producer comparing options needs to know
what the rejected one *would* have cost — that is often why they ask for a
repair rather than accepting the safe, expensive alternative.

**Deduplication by `content_digest` of the resulting state.** Two plans that
produce the same schedule are the same plan, whatever moves got them there.

**Objectives compared as `Decimal` via `str`.** Two plans meant to tie must tie.
Binary float comparison would make the frontier depend on representation error.

## The repair path

Round 1 generates, applies, validates and scores. Round 2 takes each invalid
plan, reads the rule that rejected it, and builds a repair:

```
C001 subject_ids = ("2026-09-10", "2026-09-11")
  → new_call = Sep 10 wrap 22:00 + 10h minimum + 30min buffer = Sep 11 08:30
  → compress_day checks the shortened window against the permit and the 12h cap
  → 210 + 180 + 45 scene minutes + 3 × 15 setup = 480 min, fits in 630
  → repair = ShiftCallTime(Sep 11 → 08:30), nothing shed
  → labelled B2
```

The repair is derived from the violation, not hard-coded. A rejected plan that
cannot be repaired stays in the result as evidence.

## The DEFER correction

The first version moved every downstream scene along with the blocked work.
Under strict C008 that put S28 on the same reserve day as its prerequisite S18 —
an invalid plan. Fixing it by pushing S28 to the *second* reserve day made Plan
A cost 14,900 and spend both slack days, and it was then dominated on every axis.

The right fix was narrower: **move a downstream scene only if the deferral
actually overtakes it.** S28 already sits after the reserve day, so it stays put
and DEFER costs one reserve day. That is `_family_defer` plus `_place_deferred`.

## The plate-relocation repricing

With the weights as first written, relocating two VFX plates cost 0.5 days and
0.10 risk — making RELOCATE the fastest, lowest-risk option and leaving a
three-plan frontier `{A, C, B2}`, which fails this prompt's own
`test_pareto_set_is_exactly_a_and_b2`.

Escalated rather than silently retuned. The operator chose to reprice:
`vfx_plate_relocation_slip_days` 0.5 → 2.5, and the risk weight 0.10 → 0.45 with
the others rebalanced. The reasoning is in the config file: a plate shot against
the wrong background is the only failure here discovered after wrap, when
nothing can be reshot.

## Verified output

```
  A  DEFER      valid    delay 11.38  cost 10,950  risk 0.375  PARETO
  B  SWAP       INVALID  C001: observed 9.0h, required >= 10.0h
  C  RELOCATE   valid    delay  2.50  cost 14,200  risk 0.450  dominated
  B2 repair     valid    delay  1.90  cost 12,205  risk 0.267  PARETO

  pareto = ['A', 'B2']
```

## How to run it

```bash
pytest tests/engine/simulation -v
```

## Acceptance criteria

- ✅ `test_plan_b_invalid_with_c001`
- ✅ `test_plan_b_observed_is_nine_hours`
- ✅ `test_plan_b2_is_valid`
- ✅ `test_pareto_set_is_exactly_a_and_b2`
- ✅ `test_result_is_deterministic_on_repeated_runs`
- ✅ `test_round_trace_records_c001`
- ✅ ruff + `mypy --strict` clean
- ✅ No numeric literal in scoring arithmetic
- ✅ No LLM call

## Not covered

**COMPRESS never fires as a first-class family on this fixture.** No baseline
day is over capacity, so it contributes nothing in round 1. Its real use is the
repair step. The family is implemented and reachable, but untested against a
disruption that would actually trigger it.

**`max_rounds` above 2 does nothing.** The loop is written for one repair round;
a third would need repairs-of-repairs and a cycle guard.

**The cost model is a model.** Nothing reconciles against a real budget system.
The plate-relocation penalty in particular is a judgement call, documented as
one.
