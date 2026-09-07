# Artifact 17 — Demo Scenario Runner

**Prompt:** [PROMPT_17_demo_runner.md](../prompts/PROMPT_17_demo_runner.md)
**Gate:** `SCORE`
**Outcome:** complete — three identical runs verified

---

## Plan

One command that runs the whole story with timings, a reset that is safe to
press mid-flow, and a fallback that means a quota error costs a paragraph of
prose rather than the demo.

## Files created

| File | Contents |
|---|---|
| [`scripts/run_demo.py`](../../../scripts/run_demo.py) | Reset, wait, publish, drive, print each stage with timings |
| [`scripts/emit_disruption.py`](../../../scripts/emit_disruption.py) | Publish the LOC-04 block to Confluent |
| [`simulation/narrate.py`](../../../src/pri/engine/simulation/narrate.py) | The deterministic explanation |

## Verified output

```
PRI demo  -  http://127.0.0.1:8011  -  film-001

  [  229ms /   229ms]  health  v0.1.0 sha unknown db up
  [  356ms /   584ms]  reset  version 1
  [   19ms /   603ms]  EVENT_RECEIVED  location.blocked LOC-04
  [   58ms /   661ms]  IMPACT_COMPUTED  3 scenes, blast radius 31%
  [    0ms /   661ms]  CANDIDATES_GENERATED  A, B, C
  [    0ms /   661ms]  CANDIDATE_INVALID  Plan B - C001: observed 9.0h, required >= 10.0h
  [    0ms /   661ms]  CANDIDATE_VALID  Plan B2 generated and valid
  [    0ms /   661ms]  AWAITING_APPROVAL  recommending plan-B2, frontier ['plan-A', 'plan-B2']
  [   17ms /   678ms]  APPROVED  plan-B2 by producer@nighttrain
  [  238ms /   916ms]  EXECUTING  validate_state -> validate_constraints -> validate_policy
                                  -> verify_authorization -> request_approval
                                  -> execute -> verify_result
  [    0ms /   916ms]  VERIFIED  6/6 checks green, v1 -> v2
  [    6ms /   922ms]  call sheet  Sep 11 now calls at 08:30 with ['S17', 'S18', 'S21']
```

Three consecutive runs, diffed: **identical**.

## Decisions

**`/health` is called first, deliberately.** It wakes a cold Cloud Run instance
before anything that matters is timed, so the stage timings reflect the pipeline
rather than a container start.

**`--wait-for-viewer` seconds.** Gives a presenter time to get the browser in
front of the camera before the event is published, so the screens are watching
when it lands.

**`--no-execute`** stops after recovery, leaving the approval for a human to
click on camera. That is how the video's 2:05 beat gets shot.

**`--new-id` on the emitter.** The pipeline deduplicates on `event_id`, which is
correct behaviour and inconvenient when rehearsing. A fresh id per take.

**The deterministic narrator is a real module, not a string template inline.**
`narrate.py` assembles the explanation from the same six numbers the agent would
quote — every figure in it came out of `scoring.py`. The UI shows a
`deterministic mode` badge so nobody is misled about which path ran.

## The reset

`POST /api/demo/reset` now runs the migrations *before* seeding. It started as a
`seed()` call and returned a 500 against a database whose schema had been torn
down — which is the judge's Demo button, and "the schema is not there" is not
something they can act on. Found by running this script against a database the
test suite had just dropped.

It deletes approvals, candidate plans, sessions, artifacts, audit rows, state
versions and events for the production, in foreign-key order, then re-inserts
version 1.

## How to run it

```bash
make run-api
make demo                                    # local
python scripts/run_demo.py --api https://... # hosted
python scripts/run_demo.py --wait-for-viewer 10 --no-execute
```

## Acceptance criteria

- ✅ Resets to version 1
- ✅ Publishes the disruption and prints every stage with timings
- ✅ Reset is safe to press mid-flow
- ✅ Deterministic fallback with a `deterministic mode` badge
- ✅ reset → demo → reset → demo, three times, identical results

## Not covered

**No "Demo" button in the UI top bar.** The prompt asks for one so a judge at
2am can replay the story without reading the README. The top bar has **Reset
demo**, which returns to version 1, but the judge then has to go to the
Disruption screen and there is no in-app way to *publish* the event — that is
still `scripts/run_demo.py` or `emit_disruption.py` from a terminal. This is the
clearest remaining gap in the hosted experience.

**`run_demo.py` drives the API directly, not Confluent.** It posts the event
over HTTP so the SSE stream fires on the same instance. `emit_disruption.py` is
the Kafka path and is separate. Both are real; the video should show the Kafka
one landing and then the console reacting.

**`--wait-for-viewer` sleeps rather than waiting for a subscriber.** The broker
exposes `subscriber_count`, but there is no endpoint for it, so the script
cannot actually detect that a browser attached.
