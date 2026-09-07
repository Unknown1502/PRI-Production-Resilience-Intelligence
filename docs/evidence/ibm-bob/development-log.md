# IBM Bob — Development Log

What Bob built, in the order it built it. Each prompt is one session and one
commit; the prompts themselves are in [`ibm/bob/prompts/`](../../../ibm/bob/prompts/)
and are self-contained, so any of them can be replayed in a fresh session to
regenerate its module.

## Day 1 — the deterministic core

This day is the whole product. Everything after it is presentation.

| Prompt | Built | Notable |
|---|---|---|
| **01** Repository scaffold | `pyproject.toml`, `.env.example`, package layout, `config.py`, `Makefile` | Strict mypy configured on `domain` and `engine` from the first commit, so no untyped code was ever normal here |
| **02** Domain model | `domain/models.py` — every model frozen, `ProductionState.apply()` | The `Move` discriminated union and the version chain. `apply()` is mechanical: it never validates, because validation is a separate concern with a separate test suite |
| **03** Persistence | `persistence/` — schema, async repository, `StaleStateError` | Snapshot-per-version rather than a normalised write model. Optimistic concurrency via `SELECT … FOR UPDATE` on the head row. No UPDATE or DELETE path exists on `state_versions` |
| **04** Dependency graph 🎥 | `engine/graph/dependency.py` | Descendants walked over prerequisite edges *only* — Bob's first draft walked resource edges too, which made every scene downstream of every other and the blast radius meaningless |
| **05** Seed fixture | `data/fixtures/night_train.json`, `persistence/seed.py` | Permit windows expressed as daily recurrences and expanded per shoot date; every clock time carries an explicit offset so the arithmetic is exact |
| **06** Constraint validator 🎥 | `engine/constraints/validator.py` — C001 to C010 | Each rule is a standalone function in a `RULES` list. The `observed` / `required` strings were written before the rules that produce them, which shaped the whole module |
| **07** Simulation | `engine/simulation/` — candidates, scoring, Pareto, replan | The two-round loop. Also `config/scoring.yaml`: no numeric literal appears in the scoring arithmetic |
| **08** Verification + transition | `engine/verification/verify.py`, `engine/transition.py` | Six checks; seven guards. The rollback runs inside the write transaction because the table is append-only |

## Day 2 — API, events, agent

| Prompt | Built | Notable |
|---|---|---|
| **09** FastAPI | `api/` — routes, SSE stream, typed error handlers | Constraint failures carry the rule code as a response *field*, so the frontend never has to regex an error message |
| **10** Confluent | `events/` — schemas, producer, consumer, admin, runner | Manual offset commits; dedupe at the database, not in memory; exceptions logged, uncommitted and re-raised |
| **11** Call sheet | `artifacts/call_sheet.py` | Footer carries the state version and digest, so a sheet found on a table traces back to the state that produced it |
| **12** ADK agent 🎥 | `agent/` — root agent, tools, service | Bob was told to read the installed `google-adk` package rather than write from memory. It did, and the code matches the 2.8.0 API |

## Day 3 — interface and deployment

| Prompt | Built | Notable |
|---|---|---|
| **13** Frontend scaffold | `web/` — Next.js 15, design system, overview | A production control room, not a chat app. Two signal colours, monospaced numerals on every figure |
| **14** Disruption + Recovery | The two screens that carry the demo | The rejected-plan card stays on screen and stays loud. That component is the difference between this and a schedule chatbot |
| **15** Governance + Verification + Audit | The closing half of the flow | The seven-step checklist lights up from what the transition service actually reports, not from a timer |
| **16** Cloud Run | Three Dockerfiles, `infra/deploy.sh` | Idempotent at every step, because the state you are actually in at 2am is "half deployed" |
| **17** Demo runner | `scripts/run_demo.py`, `scripts/emit_disruption.py` | Three consecutive runs produce identical output |

## Day 4 — submission

| Prompt | Built |
|---|---|
| **19** Critical-path tests | `tests/api/test_wow_scenario.py`, `tests/engine/test_transition.py`, CI with a Postgres service container |
| **20** README and evidence | `README.md`, `docs/evidence/`, `docs/hackathon/compliance-matrix.md` |
| **21** Devpost copy | `docs/hackathon/devpost.md` |

## Where Bob was corrected

Recording the misses matters more than recording the hits — they are what shows
the process was real.

**`SwapDays` swapped only the scene lists.** A day swap in production moves the
whole day: location, call, wrap and scenes. Swapping scenes alone stranded them
at a location that could not host them and left the original turnaround intact —
which is exactly the constraint the swap is supposed to stress. Corrected in
PROMPT 07's session, and it is what produces the 9.0h violation the demo turns
on.

**`ShiftCallTime` moved the wrap along with the call.** That preserved the day's
length by pushing the wrap past the location's 19:00 permit, trading a
turnaround violation for a permit violation. Wrap now holds and the day gets
shorter, which is what makes the repair meaningful.

**C008 compared dates loosely.** The rule requires a prerequisite on a strictly
earlier date. Tightening it exposed two same-day prerequisite edges in the
fixture and a real bug in the deferral family, which was dragging a downstream
scene onto a second reserve day for no reason. Both fixed; the reasoning is in
[Appendix A](../../../ibm/bob/prompts/APPENDIX_A_demo_fixture.md).

**The first cost model made relocating a VFX plate nearly free.** Under those
weights "shoot it somewhere else" dominated every axis and the Pareto frontier
collapsed to the wrong answer. Repriced — a plate against the wrong background
is the one failure discovered after wrap — and the reasoning lives in
`config/scoring.yaml` where it can be argued with.

**Two modules were replaced rather than adapted.** An early
`simulation/planner.py` and `verification/verifier.py` used different strategy
names and verification codes than the specification settled on. Rewriting was
cheaper than reconciling.

## Gates at the end of the build

```
ruff check          All checks passed
ruff format         77 files already formatted
mypy --strict       Success: no issues found in 46 source files
pytest              283 passed
web: tsc --noEmit   clean
web: eslint         No warnings or errors
web: next build     9 routes, standalone output
```
