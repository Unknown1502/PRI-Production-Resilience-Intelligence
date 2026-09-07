# Artifact 05 — Seed Fixture: The Demo Production

**Prompt:** [PROMPT_05_seed_fixture.md](../prompts/PROMPT_05_seed_fixture.md) +
[Appendix A](../prompts/APPENDIX_A_demo_fixture.md)
**Gate:** `SCORE`
**Outcome:** complete — 9 fixture-contract tests

---

## Plan

Build *Night Train to Kochi* exactly as Appendix A specifies, and a loader that
turns it into version 1. Every number in the fixture is load-bearing for the
demo, so the loader does no rounding, no defaulting and no normalisation.

## Files created

| File | Lines | Contents |
|---|---|---|
| [`data/fixtures/night_train.json`](../../../data/fixtures/night_train.json) | — | 13 scenes, 5 locations, 4 cast, 3 equipment, 9 days, the disruption |
| [`persistence/seed.py`](../../../src/pri/persistence/seed.py) | 479 | `load_fixture`, `build_state`, `build_disruption_event`, `seed` |
| `tests/conftest.py` | — | `night_train_state`, `loc04_blocked_event` — shared by every suite |
| `tests/engine/test_demo_fixture.py` | 9 tests | The fixture as a contract |

## Decisions

**Recurrence, not enumeration, in the JSON.** LOC-04's permit is written
`{"start": "07:00", "end": "19:00"}` and expanded to one concrete `TimeWindow`
per shoot date at load. Writing 26 identical windows by hand would be
unreadable and would drift. LOC-03's `12:00–02:00` crosses midnight and the
expander handles it.

**Every clock time carries `+05:30`.** The production is in Kochi. Storing naive
datetimes and hoping every comparison agrees on a timezone is how a
nine-hour turnaround silently becomes a fourteen-hour one.

**A fixed `created_at` in the test fixture.** `build_state(created_at=...)` lets
the suite pin the snapshot digest, which the dedup and verification tests need.

**Reserve days exist as empty `ShootingDay` rows.** `MoveSceneToDay` requires
its target to be a scheduled day, so Sep 16 and Sep 30 have to be in the
schedule for DEFER to target them at all.

## Fixture changes from Appendix A as originally written

Two, both forced by session 06's strict-date C008 and both confirmed with the
operator before being made:

**Same-day prerequisite edges removed.** Appendix A had `S18 → S17` (both on
Sep 10) and `S25 → S24` (both on Sep 11). Under a rule requiring a prerequisite
on a strictly *earlier* date, those made the seeded state illegal on arrival —
which would have failed this session's own acceptance criterion. Scenes shot
back-to-back on one day are ordered by the shot list, not by a dependency edge.
`S28 → S18` spans days and survives; it is the edge the demo actually turns on.

**S28 moved to a new Sep 17 studio day.** With S28 on Sep 15, deferring the
courtyard block to the Sep 16 reserve day would overtake it and drag it onto the
*second* reserve day — spending both of the production's slack days to recover
one. On the 17th it simply stays put, and DEFER costs one reserve day, which is
the trade-off the demo is about.

Appendix A was updated to match, with the reasoning written into it.

## How to run it

```bash
make bootstrap      # migrations + seed
make seed           # seed only
pytest tests/engine/test_demo_fixture.py -q
```

## Acceptance criteria

- ✅ `make seed` produces a valid version-1 state
- ✅ The seeded state has **zero HARD violations** (asserted)
- ✅ No extra scenes invented, no times rounded
- ✅ `SwapDays(Sep 10, Sep 11)` produces exactly one C001 at `observed="9.0h"`

## Not covered

**The 13 scenes are the 6 Appendix A specifies plus 7 the schedule references.**
S12, S13, S15, S16, S29, S30 and S31 appear in the baseline day table but have
no attributes in the appendix, so their durations, cast and equipment were
chosen to be consistent with their day's location and to keep the baseline
legal. They are filler, and a reviewer should treat their specific numbers as
arbitrary — unlike the six scenes the scenario turns on.

**Crew is one `Person` row.** `CREW-MAIN` stands in for 34 people at a blended
rate. C001 and C002 are per-unit rules so this is sufficient, but nothing models
an individual crew member's availability.
