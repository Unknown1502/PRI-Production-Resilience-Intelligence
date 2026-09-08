# What we would build next

Every item here names the file it would change and what would have to be true
for it to be finished. Nothing on this page is a direction of travel; each one
is a piece of work someone could pick up on Monday.

The list is ordered by what a production would hit first, not by what is most
interesting to build.

---

## 1 · A second unit

**Today.** The validator already refuses to double-book. C005 catches two units
at one location on the same date and C007 catches the same equipment on two
units, and both run on every candidate. What is missing is the other half: no
strategy family ever *generates* a plan that puts work on a second unit, so the
rules guard against a plan shape the planner cannot produce.

**The work.** A fifth family in
[`engine/simulation/candidates.py`](../src/pri/engine/simulation/candidates.py)
that splits a blocked day across units rather than moving it in time — the move
a real 1st AD reaches for before deferring anything, because it costs days
rather than schedule. It needs a `SplitToUnit` move in
[`domain/models.py`](../src/pri/domain/models.py), a crew model with more in it
than a single `MAIN` row, and a scoring term for the cost of standing a second
crew up.

**Done when** a `location.blocked` event on a day with two eligible scenes
produces a candidate that shoots one on `SECOND` the same day, and C005 and
C007 reject the version of that plan which reuses the same camera.

---

## 2 · Costs that reconcile against a real budget

**Today.** [`config/scoring.yaml`](../config/scoring.yaml) holds the rates —
delay, cost, risk, capacity, repair — and no numeric literal appears in the
scoring arithmetic, so every figure on screen traces to a line in that file.
They are a production's own estimates, and nothing checks them against what was
actually spent.

**The work.** An adapter behind `incremental_cost` that reads committed actuals
from a production accounting system — Movie Magic Budgeting and Sage are the
two that matter — and reports the variance between what a plan was scored at
and what the day cost. The scoring stays deterministic; the rates stop being
assertions.

**Done when** a producer can see, for a plan executed last week, the figure PRI
predicted beside the figure the ledger recorded, and the difference is
attributable to a named line.

**Why it matters more than it sounds.** The plate-relocation penalty is the one
number in the model that is a judgement rather than a derivation. It is
documented as such. A reconciliation loop is what would let it stop being one.

---

## 3 · The remaining three disruption types

**Today.** All five event types compute impact and run the full pipeline.
`location.blocked` and `actor.unavailable` are hardened, each with a narrated
scenario test. `equipment.failed`, `weather.changed` and `crew.unavailable`
generate valid, scored plans that nobody has checked for usefulness — and they
are refused by the live-injection endpoint for exactly that reason.

**The work.** The same pass that hardened `actor.unavailable`, three more
times. That pass found a real defect rather than merely adding coverage: a
disruption's claim was never written into the state being validated, so C003
had nothing to check and RELOCATE produced plans that moved a blocked scene to
a different location and left it on the day the actor was away. See
[`engine/simulation/projection.py`](../src/pri/engine/simulation/projection.py).

Each of the three needs its own projection and its own scenario test:

| Type | What the event asserts | The rule that should catch a bad plan |
|---|---|---|
| `equipment.failed` | this kit is out of service for a window | C006 equipment window |
| `crew.unavailable` | this unit cannot work these dates | C001 turnaround, C002 daily hours |
| `weather.changed` | exteriors are not shootable in a window | no rule models weather yet |

The third is the interesting one. There is no constraint for weather, so
`ext_weather_exposure` is a scoring term rather than a rule, and a plan that
moves an exterior into a storm is *penalised* rather than *refused*. Deciding
which of those it should be is a product question, not an engineering one.

**Done when** each of the three has a scenario test of the same shape as
[`test_actor_unavailable_scenario.py`](../tests/engine/simulation/test_actor_unavailable_scenario.py),
and the injection endpoint's `SUPPORTED_EVENT_TYPES` can grow without anything
else changing.

---

## 4 · Horizontal scale, and retiring the pin

**Today.** The SSE broker in
[`api/stream.py`](../src/pri/api/stream.py) keeps its subscribers in a
process-local dict, so `pri-api` is pinned to one instance. That is enforced
rather than hoped for — `test_cloud_run_contract.py` fails if the instance
count is raised while the broker is still process-local, and
`scripts/verify_live_demo.py` proves two concurrent viewers see the same ten
stages against the live URL.

One instance at concurrency 40 carries a demo. It would not carry a studio, and
it is a single point of failure for a system whose selling point is resilience.

**The work.** Redis pub/sub behind the same `StreamBroker` interface, or a
Confluent consumer per instance fanning into local queues. The interface does
not change; `publish` and `subscribe` already have the right shape.

**Done when** the pin is lifted, the contract test is deleted on purpose, and
`verify_live_demo.py` still passes with `--max-instances 4`.

---

## 5 · An approver who is actually a person

**Today.** Approval is a name in a field. The transition service has a
`verify_authorization` step, fourth of seven, and it checks the name against a
permitted list — but nothing establishes that the person typing it is who they
say they are. The audit records `producer@nighttrain` because someone typed
`producer@nighttrain`.

**The work.** Identity-Aware Proxy in front of the console, the approver taken
from the verified header rather than a form field, and the permitted list keyed
to real accounts. The seven guards do not change; the fourth one starts
meaning something.

**Done when** an approval carries an identity the organisation already trusts,
and a screenshot of the audit log is evidence rather than testimony.

**Deliberately not half-built.** A name in a box is obviously a name in a box.
A login page that authenticates nothing would be worse, because it would look
like it did.

---

## 6 · Call sheets that reach the crew

**Today.** Call sheets are durable. They go to a Cloud Storage bucket keyed
`<production>/v<version>/<date>.pdf`, so the `artifacts` row and the document it
points at survive the same deploy — before that, the row outlived the file and
the audit trail claimed a publication it could not produce.

What is missing is distribution. The API re-renders a sheet on request rather
than issuing a signed URL to the stored one, there is no retention policy on
the bucket, and nothing sends anything to anyone.

**The work.** Signed URLs with a sensible expiry, a lifecycle rule on the
bucket, and a delivery step in
[`engine/transition.py`](../src/pri/engine/transition.py) after verification —
email or SMS to the cast and crew a changed day actually affects, which the
impact report already knows.

**Done when** executing a plan puts a new call sheet in the hands of the people
whose day changed, and the audit log records who it went to.

---

## 7 · Another domain

PRI is a constrained-replanning engine with an approval gate. The film nouns
are a skin, and a thin one: the constraint pack in
[`config/policies.yaml`](../config/policies.yaml) and the ten rules that read
it, the call-sheet renderer in
[`artifacts/call_sheet.py`](../src/pri/artifacts/call_sheet.py), and the
workbook schema in [`importer/`](../src/pri/importer/). Everything above them —
the append-only version chain, the blast-radius walk, the four strategy
families, the Pareto frontier, the seven-step transition, the six verification
checks — never learns what a scene is.

The honest test of that claim is to swap those three and see what breaks.
**Field service dispatch** is the cheapest first attempt: a job is a scene, an
engineer is a cast member, skills and parts and travel time are the constraint
pack, and tomorrow's route sheet is the call sheet. A week's work, and it would
either prove the boundary or find the film assumptions we have not noticed.

---

## What we would not build

**A chatbot that answers "what should we do?"** It was the obvious build and it
is the wrong product. A model that invents a schedule and a cost reads
authoritative and cannot be checked, and nobody reschedules a 34-person crew on
that. The architecture exists to make that impossible rather than merely
discouraged.

**A model that authors moves.** The agent's only planning input is
`strategy_hints: list[str]`, matched against four constants. Widening that is
the one change that would make every number on screen untrustworthy, and no
feature is worth it.

**watsonx Orchestrate A2A.** Written, timeboxed, and deliberately not built —
see [`PROMPT_18_watsonx_a2a.md`](../ibm/bob/prompts/PROMPT_18_watsonx_a2a.md),
whose first lines record the decision. Bob and Confluent already satisfy the
IBM track, and building a row we do not need was the worst trade available
before a deadline.
