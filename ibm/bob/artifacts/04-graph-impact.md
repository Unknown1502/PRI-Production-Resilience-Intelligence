# Artifact 04 — Dependency Graph & Impact Engine

**Prompt:** [PROMPT_04_graph_impact.md](../prompts/PROMPT_04_graph_impact.md)
**Gate:** `SCORE` · 🎥 **session recorded in full**
**Outcome:** complete — 41 tests

---

## Plan

Build a NetworkX DiGraph from a `ProductionState` with typed nodes and typed
edges, then answer one question against it: given a disruption, what breaks, and
what breaks because of what broke?

## Files created

| File | Lines | Contents |
|---|---|---|
| [`engine/graph/dependency.py`](../../../src/pri/engine/graph/dependency.py) | 599 | `build_graph`, `impact_of`, `graph_payload` |
| `tests/engine/graph/test_dependency.py` | 41 tests | Per event type, plus graph shape and payload |

`ImpactReport` was added to `domain/models.py` in this session.

Node types: `scene`, `person`, `location`, `equipment`, `shooting_day`.
Edge kinds: `REQUIRES_CAST`, `REQUIRES_CREW`, `REQUIRES_LOCATION`,
`REQUIRES_EQUIPMENT`, `PREREQUISITE`, `SCHEDULED_ON`.

## Decisions

**Descendants walk `PREREQUISITE` edges only.** The first version walked every
edge transitively, which produced a blast radius of nearly 100% on every event:
scene A needs actor P01, P01 is in scene B, therefore B is downstream of A.
That is not what downstream means. Restricting the walk to prerequisite edges is
what makes the number mean something — the fixture's LOC-04 block reports 31%,
and it is 31% because four scenes out of thirteen are genuinely affected.

This is the single most important line in the module and it is a one-word
difference from a version that looks like it works.

**Per-event-type dispatch.** Five resolvers, one per `event_type`, each
answering "which scenes does this directly block?" before the shared descendant
walk runs. `location.blocked` intersects location with a time window;
`weather.changed` takes every EXT scene in the window; and so on.

**`graph_payload` is shaped for the frontend, in the backend.** Node status is
computed here as `ok` / `impacted` / `downstream`, so the browser colours from a
field rather than re-deriving the impact set in TypeScript. One implementation
of "what is affected", not two.

## How to run it

```bash
pytest tests/engine/graph -q
```

Against the demo fixture:

```python
from pri.persistence.seed import build_state, build_disruption_event
from pri.engine.graph.dependency import impact_of

report = impact_of(build_state(), build_disruption_event())
# directly_affected: S17, S18, S21
# downstream:        S28
# blast_radius:      0.308
```

## Acceptance criteria

- ✅ `impact_of` is pure — no IO, deterministic
- ✅ Blocking LOC-04 on the fixture date returns exactly the expected scene set
- ✅ Descendants computed with `nx.descendants` over prerequisite edges only
- ✅ `graph_payload` shaped for `@xyflow/react`
- ✅ No LLM call

## Not covered

**`affected_days` includes days holding downstream scenes, not just the blocked
day.** For the fixture that means Sep 10 *and* Sep 17. That is defensible — those
days are affected — but it surprised the session-07 tests, which assumed the
tuple was the blocked days alone. Documented rather than changed, because the
candidate generator uses `min(affected_days)` and wants the blocked day.

**No cycle detection.** A prerequisite cycle in a fixture would make
`nx.descendants` return the whole component rather than raising. The candidate
generator's `_dependency_order` degrades gracefully on a cycle, but nothing
tells you the fixture is broken.
