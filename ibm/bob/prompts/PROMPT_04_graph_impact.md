# PROMPT 04 — Dependency Graph & Impact Engine

## Task

Build `src/pri/engine/graph/dependency.py` using NetworkX.

## Graph Structure

Build a `DiGraph` from a `ProductionState` with typed nodes and typed edges:

### Node types
- `scene`
- `person`
- `location`
- `equipment`
- `shooting_day`

### Edge types
| Edge | Kind |
|------|------|
| scene → person | `REQUIRES_CAST` / `REQUIRES_CREW` |
| scene → location | `REQUIRES_LOCATION` |
| scene → equipment | `REQUIRES_EQUIPMENT` |
| scene → scene | `PREREQUISITE` |
| shooting_day → scene | `SCHEDULED_ON` |

## API

```python
build_graph(state) -> nx.DiGraph
impact_of(state, event) -> ImpactReport
```

## ImpactReport (new Pydantic model in domain/models.py)

```python
ImpactReport(
    event_id,
    directly_affected_scene_ids,
    downstream_scene_ids,
    affected_cast_ids,
    affected_crew_ids,
    affected_location_ids,
    affected_equipment_ids,
    affected_days: list[date],
    downstream_dependency_count: int,
    blast_radius: float   # affected scenes / total remaining scenes
)
```

## Impact Resolution per event_type

| event_type | Direct impact rule |
|---|---|
| `location.blocked` | Every scene scheduled at that location inside the blocked window, plus their prerequisite-descendants |
| `actor.unavailable` | Every scene with that cast_id inside the window, plus descendants |
| `equipment.failed` | Every scene requiring that equipment inside the window |
| `crew.unavailable` | Every scene on the affected unit's days in the window |
| `weather.changed` | Every EXT scene scheduled in the window |

## Descendant Rule

Descendants must be computed with `nx.descendants` over **PREREQUISITE edges only** —
do not walk resource edges transitively or the blast radius becomes meaningless.

## Frontend Payload

Also expose:

```python
graph_payload(state, impact) -> dict
```

Shaped for `@xyflow/react`:
```json
{
  "nodes": [{"id": "...", "type": "...", "label": "...", "status": "..."}],
  "edges": [{"id": "...", "source": "...", "target": "...", "kind": "..."}]
}
```

`status` ∈ `{"ok", "impacted", "downstream"}` — the frontend colours from this.

## Tests

Write tests using the seed fixture. Blocking `LOC-04` on the fixture date must
return exactly the expected scene set.

## Acceptance Criteria

- `impact_of` is pure (no IO), deterministic, and covered by tests

## Constraints

- DO NOT call an LLM here. Ever.
