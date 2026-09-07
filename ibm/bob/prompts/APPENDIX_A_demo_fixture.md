# Appendix A — The Demo Fixture

> Paste this whole appendix into **PROMPT 05**. Every value is chosen so the demo
> produces the exact failure and repair described. Reproduce it exactly.
>
> **Every figure below is copied from an actual run**, not authored by hand.
> Regenerate them with `pytest tests/engine/simulation/test_recovery_scenario.py`
> if the fixture or `config/scoring.yaml` changes.

## Production

`film-001` — *Night Train to Kochi*, currency **USD**, shoot **Sep 8 – Oct 3, 2026**.
Reserve days: **Sep 16**, **Sep 30**.

## Locations

| id | name | day rate | permit window | INT/EXT | time of day |
|---|---|---|---|---|---|
| LOC-01 | Studio Stage A | 3,000 | always | INT | DAY, NIGHT |
| LOC-02 | Fort Kochi Street | 4,200 | 06:00–20:00 daily | EXT | DAY, DUSK |
| LOC-03 | Harbour Warehouse | 3,800 | 12:00–02:00 daily | INT, EXT | NIGHT, DUSK |
| LOC-04 | Mattancherry Courtyard | 6,500 | 07:00–19:00 daily | EXT | DAY |
| LOC-07 | Standing Set — Warehouse INT | 1,200 | always | INT | DAY, NIGHT |

## Cast

| id | name | rate/day | unavailable |
|---|---|---|---|
| P01 | Arun (lead) | 2,400 | — |
| P02 | Meera (lead) | 2,600 | **Sep 12–14** |
| P03 | Dev | 1,100 | — |
| P04 | Kiran | 900 | — |

## Crew

Single **MAIN** unit, 34 people, blended overtime rate **210/hour**.

## Equipment

| id | name | rate/day | availability |
|---|---|---|---|
| CAM-01 | Alexa 35 | 950 | always |
| CRANE-01 | Technocrane | 1,800 | **rental window Sep 9–11 only** |
| STEADI-01 | Steadicam | 700 | always |

## Scenes (the subset that matters)

| id | # | INT/EXT | ToD | min | location | cast | prereq | VFX plate |
|---|---|---|---|---|---|---|---|---|
| S17 | 17 | EXT | DAY | 210 | LOC-04 | P01, P02 | — | yes |
| S18 | 18 | EXT | DAY | 180 | LOC-04 | P01, P03 | — | yes |
| S21 | 21 | EXT | DAY | 45 | LOC-04 | P04 | — | no |
| S24 | 24 | INT | NIGHT | 240 | LOC-03 | P01, P04 | — | no |
| S25 | 25 | EXT | NIGHT | 195 | LOC-03 | P01, P03 | — | no |
| S28 | 28 | INT | DAY | 150 | LOC-01 | P02 | **S18** | no |

> **On prerequisites.** Rule C008 requires a prerequisite on a *strictly earlier
> date*. Scenes shot back-to-back on the same day are ordered by the day's shot
> list, not by a dependency edge — a dependency that survives only because of
> the order two slugs happen to sit in is not one the production can rely on,
> because the running order changes on the morning. So S17/S18 and S24/S25 carry
> no edge between them. **S28 → S18 spans days and is the dependency the demo
> turns on**: it is why the impact report reaches past the blocked day.

## Schedule (baseline, valid)

| day | date | location | call | wrap | scenes |
|---|---|---|---|---|---|
| 1 | Sep 8 | LOC-01 | 08:00 | 19:00 | S12, S13 |
| 2 | Sep 9 | LOC-02 | 07:00 | 18:00 | S15, S16 |
| **3** | **Sep 10** | **LOC-04** | **07:00** | **19:00** | **S17, S18, S21** |
| **4** | **Sep 11** | **LOC-03** | **12:00** | **22:00** | **S24, S25** |
| 5 | Sep 12 | LOC-01 | 08:00 | 18:00 | S30, S31 |
| 6 | Sep 15 | LOC-01 | 08:00 | 19:00 | S29 |
| — | Sep 16 | LOC-04 | 07:00 | 19:00 | *reserve* |
| 7 | Sep 17 | LOC-01 | 08:00 | 18:00 | S28 |
| — | Sep 30 | LOC-04 | 07:00 | 19:00 | *reserve* |

Baseline turnaround Sep 10 wrap 19:00 → Sep 11 call 12:00 = **17.0h** ✓

> **Why S28 sits on Sep 17.** It has to shoot after S18, and deferring the
> courtyard block lands S18 on the Sep 16 reserve day. With S28 on the 15th the
> deferral would overtake it and drag it onto the *second* reserve day, spending
> both of the production's slack days to recover one. On the 17th it simply
> stays put, and DEFER costs one reserve day — which is the trade-off the demo
> is actually about.

## The Disruption

`location.blocked`, **LOC-04**, **Sep 10 00:00 → Sep 11 00:00**,
source `location_manager`, severity `high` (0.8).
Municipal permit withdrawn for a festival procession.

## Expected Impact

- Directly affected: **S17, S18, S21**
- Downstream: **S28** (prerequisite of S18)
- Cast: **P01, P02, P03, P04**
- Blocked day: **Sep 10**

## Expected Candidates

| plan | family | moves | valid | delay | cost | risk | frontier |
|---|---|---|---|---|---|---|---|
| **A** | DEFER | S17, S18, S21 → Sep 16 | ✅ | 11.38 d | +10,950 | 0.375 | **Pareto — cheapest** |
| **B** | SWAP | Sep 10 ↔ Sep 11 | ❌ | 1.90 d | +12,520 | 0.267 | **C001**: observed **9.0h**, required **≥ 10.0h** |
| **C** | RELOCATE | S17, S18, S21 → LOC-02 | ✅ | 2.50 d | +14,200 | 0.450 | dominated by B2 on all three axes |
| **B2** | repair of B | SWAP + ShiftCallTime(Sep 11 → 08:30) | ✅ | 1.90 d | +12,205 | 0.267 | **Pareto — protects the schedule** |

Pareto frontier = **{A, B2}**.

## The arithmetic to verify in tests

**Plan B's rejection.** A swap moves the whole day — location, call, wrap and
scenes. Sep 10 takes the warehouse's 12:00–22:00; Sep 11 takes the courtyard's
07:00 call. Wrap 22:00 → call 07:00 = **9.0h** against a 10.0h minimum.

**Plan B2's repair.** Derived from the rule, not hard-coded: the planner reads
`C001`'s subject days, computes
`Sep 10 wrap 22:00 + 10h minimum + 30min buffer = Sep 11 08:30`, then checks the
day still fits. LOC-04's permit ends at 19:00, so the window is
**08:30–19:00 = 10.5h**, inside the 12h cap; S17 (210) + S18 (180) + S21 (45)
plus three 15-minute setups is **480 minutes**, which fits in 630. Nothing has
to be shed. Turnaround is restored to **10.5h ≥ 10.0h** ✓

**Why C is dominated.** Relocating two VFX plates is priced as what it is — a
failure discovered in post, when there is nothing left to reshoot with. B2 beats
it on delay (1.90 vs 2.50), cost (12,205 vs 14,200) and risk (0.267 vs 0.450),
so C is a legal plan that is simply not worth it. It stays on screen as a real
option a producer can see themselves rejecting.

## Gemini's Recommendation

**B2** — it holds the schedule to 1.90 days of slip for 12,205, where Plan A
saves 1,255 but pushes the S17/S18 plates eleven days out, past the vendor's
lock date.
