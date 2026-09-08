# PRI — Production Resilience Intelligence

> **A deterministic agentic digital twin: Gemini interprets, software decides,
> and nothing reaches the schedule that has not passed seven gates and six
> checks.**
>
> Demonstrated on a film production, where a blocked location on Tuesday costs
> three scenes, a VFX delivery date and forty thousand dollars — and the
> replan has to be defensible by 19:00.

**Live console:** <https://pri-web-3r5eyp275a-uc.a.run.app>

## Inspiration

A lost shooting day on a mid-budget feature costs forty to eighty thousand
dollars, and what makes it expensive is rarely the day — it is what the day was
holding up. A blocked courtyard costs three scenes plus the VFX plates they
capture, which a vendor has locked a delivery date around. The first AD works
this out on a whiteboard at 19:00 the night before, from memory, while their
phone rings.

The obvious build is a chatbot answering "what should we do?". That is the
wrong product, and dangerous: a model that invents a schedule and a cost reads
authoritative and cannot be checked. Nobody reschedules a 34-person crew on
that.

## What it does

PRI is a stateful digital twin of a production. When reality changes it:

1. **Reads the disruption** off a Confluent topic and computes blast radius with
   a NetworkX dependency graph.
2. **Generates options** from four enumerated strategy families — defer, swap,
   relocate, compress.
3. **Validates each one** against ten deterministic rules: crew turnaround,
   daily hours, cast availability, permits, rental windows, prerequisite
   ordering, environment matching.
4. **Rejects its own plans, and repairs them.** In the demo the swap collapses
   crew turnaround to nine hours against a ten-hour minimum. The loop reads the
   rule that rejected it, computes a legal call time from the minimum plus a
   buffer, checks the shortened day still fits the location's permit, and emits
   a repaired plan.
5. **Ranks survivors on a Pareto frontier** over delay, cost and risk, so a
   producer sees trade-offs rather than one answer.
6. **Refuses to execute** without approval — seven guards in fixed order, each
   audited before the next runs.
7. **Verifies what it wrote** with six checks running *inside* the write
   transaction; a failure aborts it, so the version was never written.
8. **Regenerates the call sheets**, stamped with state version and digest.

Gemini reads the disruption, chooses which families to explore, and explains the
result. That is all it does.

## How we built it

**The design thesis is the split.** Deterministic software computes every
number; Gemini narrates numbers handed to it. Enforced, not documented:

- The agent's only planning input is `strategy_hints: list[str]`, matched
  against four constants. It cannot author a move or return a figure.
- No numeric literal exists in the scoring arithmetic — every rate and weight
  comes from `config/scoring.yaml`, auditable in one file.
- A test AST-walks every engine module and asserts no AI SDK import reaches the
  code producing numbers; another re-runs the planner independently and asserts
  every score in the agent's tool payload matches exactly.

**Stack.** Python 3.12, FastAPI, frozen Pydantic v2, async SQLAlchemy over
PostgreSQL with an append-only `state_versions` table, NetworkX,
`confluent-kafka`, native `google-adk` with Gemini on Vertex AI, ReportLab, and
a Next.js 15 console using `@xyflow/react` — on three Cloud Run services.

**IBM Bob** built this from a twenty-two-prompt pack, twenty-one of which
were run — archived in `ibm/bob/prompts/` with an artifact per session in
`ibm/bob/artifacts/`. One prompt, one session, one commit. Specifically: the
typed domain model and immutable `apply()`; the persistence layer with
optimistic concurrency and event idempotency; the NetworkX impact engine; the
ten constraint rules and their violation strings; the strategy families,
scoring model and Pareto frontier; the replanning loop and its repair path; the
verification engine and seven-guard transition service; the FastAPI surface and
SSE stream; the Confluent producer and consumer; the ADK agent; and the console.

**Confluent** is the event fabric: four topics, a consumer that deduplicates on
`event_id` at the database and commits offsets manually only after a recovery
session exists.

## Challenges

**Two of our own specs contradicted each other, and the tests caught it.** We
tightened the prerequisite rule to require a strictly earlier date — correct,
because a dependency surviving only through shot-list order is not one you can
rely on. That made our fixture illegal and made deferral spend both reserve days
to recover one. Fixing it meant changing the fixture, not weakening the rule.

**Pricing honesty.** Our first cost model made relocating a VFX plate nearly
free, so "shoot it somewhere else" dominated every axis. It should not: a plate
against the wrong background is the one failure discovered after wrap. We
repriced it in config, and documented why.

**Rollback on an append-only table.** Verification must run after the write to
mean anything, but there is no DELETE path — so the checks run inside the
insert's transaction, and a failure aborts it.

## Accomplishments

The rejection is real and on screen. `Plan B — C001: observed 9.0h, required
>= 10.0h` comes from the validator to the browser unreformatted, because the
narration reads it aloud and a UI that turns "9.0h" into "9 hours" makes the
narrator wrong.

The loop runs in 1.3 seconds and three consecutive runs are byte-identical,
asserted by a test. 283 tests pass, `mypy --strict` is clean across 46 modules,
and the six assertions the video narrates *are* tests — if the video is wrong,
CI is red.

## What we learned

Containment is a design feature. Deciding early that the LLM could only pass one
of four strings made every later decision easier, and made the system
explainable to people who distrust language models — on a film set, everyone.

Writing the failure strings before the rules was the right order — every rule
then had to say what it saw and what it wanted, in a sentence a producer
accepts. And a rejected plan is worth more on screen than a hidden one: the
visible failure is what makes the valid options believable.

## What's next

Second-unit scheduling — C005 and C007 already refuse to double-book, but no
strategy family generates a plan that uses a second unit, so the rules guard a
shape the planner cannot produce. A budget integration so `incremental_cost`
reconciles against actuals. The three disruption types that produce valid plans
nobody has checked for usefulness, which is why the typed-input endpoint
refuses them. Redis behind the stream broker, so the single-instance pin can be
lifted. An identity provider, so the fourth of seven guards starts meaning
something. And call sheets that actually reach the crew whose day changed.

Each of those names the file it would change in
[docs/future.md](../future.md), along with what we would **not** build: a
chatbot that answers "what should we do?", and a model that authors moves.

And another domain, because the engine is not really about film. A dependency
graph of committed work, a disruption, hard constraints, a cost model and an
approval gate is the shape of a manufacturing changeover, a clinical trial site
schedule or a field service dispatch board just as much as a shooting
schedule — the film-specific part is a rule pack, a PDF renderer and an
importer schema, and the README names all three by path.

## Built with

`python` · `fastapi` · `pydantic` · `sqlalchemy` · `postgresql` · `networkx` ·
`google-adk` · `gemini` · `vertex-ai` · `confluent-kafka` · `reportlab` ·
`next.js` · `typescript` · `tailwindcss` · `xyflow` · `google-cloud-run` ·
`cloud-sql` · `secret-manager` · `ibm-bob` · `docker`

---

*Data sources: none external. The demo production, "Night Train to Kochi", is a
synthetic fixture — thirteen scenes, five locations, four cast, nine shooting
days. Every figure quoted above is copied from an actual run.*
