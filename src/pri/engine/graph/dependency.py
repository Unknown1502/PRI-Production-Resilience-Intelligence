"""Dependency graph engine for PRI.

Builds a typed ``nx.DiGraph`` from a ``ProductionState`` and computes
deterministic impact reports for disruption events.

Architecture laws:
- No LLM calls anywhere in this module.
- All numbers (blast_radius, counts) are computed here, never by an LLM.
- ``impact_of`` is a pure function: no IO, no side-effects, no randomness.

Node naming convention (avoids collisions between entity kinds):
    scene:         ``"scene:<id>"``
    person:        ``"person:<id>"``
    location:      ``"loc:<id>"``
    equipment:     ``"equip:<id>"``
    shooting_day:  ``"day:<YYYY-MM-DD>"``

Edge kinds (stored as ``graph[u][v]["kind"]``):
    REQUIRES_CAST, REQUIRES_CREW, REQUIRES_LOCATION, REQUIRES_EQUIPMENT,
    PREREQUISITE, SCHEDULED_ON
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import networkx as nx

from pri.domain.models import ImpactReport

if TYPE_CHECKING:
    from pri.domain.models import DisruptionEvent, ProductionState

# ---------------------------------------------------------------------------
# Node / edge kind constants
# ---------------------------------------------------------------------------

_KIND_REQUIRES_CAST = "REQUIRES_CAST"
_KIND_REQUIRES_CREW = "REQUIRES_CREW"
_KIND_REQUIRES_LOCATION = "REQUIRES_LOCATION"
_KIND_REQUIRES_EQUIPMENT = "REQUIRES_EQUIPMENT"
_KIND_PREREQUISITE = "PREREQUISITE"
_KIND_SCHEDULED_ON = "SCHEDULED_ON"

_NODE_TYPE_SCENE = "scene"
_NODE_TYPE_PERSON = "person"
_NODE_TYPE_LOCATION = "location"
_NODE_TYPE_EQUIPMENT = "equipment"
_NODE_TYPE_DAY = "shooting_day"

# Status values used by graph_payload (for @xyflow/react)
_STATUS_OK = "ok"
_STATUS_IMPACTED = "impacted"
_STATUS_DOWNSTREAM = "downstream"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_graph(state: ProductionState) -> nx.DiGraph:
    """Build a typed directed dependency graph from a ``ProductionState``.

    Node attributes:
        type (str): one of ``"scene"``, ``"person"``, ``"location"``,
                    ``"equipment"``, ``"shooting_day"``
        label (str): human-readable name / identifier

    Edge attributes:
        kind (str): one of the edge-kind constants defined above.

    Inputs:
        state: A fully populated :class:`~pri.domain.models.ProductionState`.

    Outputs:
        A ``networkx.DiGraph`` with nodes for every entity and typed edges.
        The graph is a new object on every call; the state is not mutated.

    Failure modes:
        Does not raise for missing cross-references (e.g. a scene references a
        location_id not in ``state.locations``).  The node is still added;
        callers that need referential integrity should validate the state first.
    """
    g: nx.DiGraph = nx.DiGraph()

    # ── Person nodes ────────────────────────────────────────────────────────
    for person in state.people:
        g.add_node(
            f"person:{person.id}",
            type=_NODE_TYPE_PERSON,
            label=person.name,
            role=person.role,
            entity_id=person.id,
        )

    # ── Location nodes ───────────────────────────────────────────────────────
    for loc in state.locations:
        g.add_node(
            f"loc:{loc.id}",
            type=_NODE_TYPE_LOCATION,
            label=loc.name,
            entity_id=loc.id,
        )

    # ── Equipment nodes ──────────────────────────────────────────────────────
    for equip in state.equipment:
        g.add_node(
            f"equip:{equip.id}",
            type=_NODE_TYPE_EQUIPMENT,
            label=equip.name,
            entity_id=equip.id,
        )

    # ── Scene nodes + resource edges ─────────────────────────────────────────
    person_roles: dict[str, str] = {p.id: p.role for p in state.people}

    for scene in state.scenes:
        sn = f"scene:{scene.id}"
        g.add_node(
            sn,
            type=_NODE_TYPE_SCENE,
            label=f"Sc{scene.number} {scene.slug}",
            int_ext=scene.int_ext,
            time_of_day=scene.time_of_day,
            location_id=scene.location_id,
            entity_id=scene.id,
        )

        # Cast / crew edges. Both lists are walked: a scene may name specific
        # crew (a stunt coordinator, a Steadicam operator) as well as its cast,
        # and either one going unavailable blocks the scene.
        for pid in (*scene.cast_ids, *scene.crew_ids):
            role = person_roles.get(pid, "CAST")
            kind = _KIND_REQUIRES_CAST if role == "CAST" else _KIND_REQUIRES_CREW
            _ensure_person_node(g, pid)
            g.add_edge(sn, f"person:{pid}", kind=kind)

        # Location edge
        _ensure_location_node(g, scene.location_id)
        g.add_edge(sn, f"loc:{scene.location_id}", kind=_KIND_REQUIRES_LOCATION)

        # Equipment edges
        for eid in scene.equipment_ids:
            _ensure_equipment_node(g, eid)
            g.add_edge(sn, f"equip:{eid}", kind=_KIND_REQUIRES_EQUIPMENT)

        # Prerequisite scene edges
        for prereq_id in scene.prerequisite_scene_ids:
            # prereq must be shot BEFORE this scene:  prereq -> scene
            g.add_edge(f"scene:{prereq_id}", sn, kind=_KIND_PREREQUISITE)

    # ── Shooting day nodes + SCHEDULED_ON edges ───────────────────────────────
    for day in state.schedule.days:
        dn = f"day:{day.date.isoformat()}"
        g.add_node(
            dn,
            type=_NODE_TYPE_DAY,
            label=day.date.isoformat(),
            date=day.date,
            location_id=day.location_id,
            unit=day.unit,
            entity_id=day.date.isoformat(),
        )
        for sid in day.scene_ids:
            g.add_edge(dn, f"scene:{sid}", kind=_KIND_SCHEDULED_ON)

    return g


# ---------------------------------------------------------------------------
# Impact analysis
# ---------------------------------------------------------------------------


def impact_of(state: ProductionState, event: DisruptionEvent) -> ImpactReport:
    """Compute the deterministic blast radius of a disruption event.

    This function is **pure**: no IO, no randomness, no LLM calls.  Given the
    same inputs it always returns the same ``ImpactReport``.

    The algorithm:
    1. Build the graph from ``state``.
    2. Identify directly-affected scenes from the event type and payload.
    3. Walk PREREQUISITE edges forward with ``nx.descendants`` to find scenes
       that depend on the directly-affected ones and cannot therefore proceed.
    4. Collect all affected resources (cast, crew, locations, equipment, days).
    5. Compute blast_radius = (directly + downstream) / total scenes.

    Inputs:
        state: The current :class:`~pri.domain.models.ProductionState`.
        event: The :class:`~pri.domain.models.DisruptionEvent` to analyse.

    Outputs:
        An :class:`~pri.domain.models.ImpactReport` with all fields populated.

    Failure modes:
        Raises ``ValueError`` if ``event.event_type`` is unrecognised (should
        not occur if the domain model is validated correctly).
        Never raises for missing cross-references; simply returns empty sets.
    """
    g = build_graph(state)

    # Build lookup indices for O(1) access
    scene_by_id = {s.id: s for s in state.scenes}
    person_by_id = {p.id: p for p in state.people}

    # Map: date -> set[scene_id] for fast window queries
    day_to_scenes: dict[date, set[str]] = {}
    for day in state.schedule.days:
        day_to_scenes[day.date] = set(day.scene_ids)

    # Map: scene_id -> date
    scene_to_day: dict[str, date] = {}
    for day in state.schedule.days:
        for sid in day.scene_ids:
            scene_to_day[sid] = day.date

    payload = event.payload

    # ── Step 1: Resolve directly-affected scene IDs ──────────────────────────
    directly_affected: set[str] = _resolve_direct_impact(
        event=event,
        state=state,
        day_to_scenes=day_to_scenes,
        payload=payload,
    )

    # ── Step 2: Downstream scenes via PREREQUISITE edges only ────────────────
    prerequisite_graph = _prerequisite_subgraph(g)
    downstream: set[str] = set()
    for sid in directly_affected:
        node = f"scene:{sid}"
        if node in prerequisite_graph:
            for desc_node in nx.descendants(prerequisite_graph, node):
                desc_id = prerequisite_graph.nodes[desc_node].get("entity_id")
                if desc_id and desc_id not in directly_affected:
                    downstream.add(desc_id)

    all_affected = directly_affected | downstream

    # ── Step 3: Collect affected resources from all affected scenes ───────────
    affected_cast: set[str] = set()
    affected_crew: set[str] = set()
    affected_locations: set[str] = set()
    affected_equipment: set[str] = set()
    affected_days: set[date] = set()

    for sid in all_affected:
        scene = scene_by_id.get(sid)
        if scene is None:
            continue
        for pid in (*scene.cast_ids, *scene.crew_ids):
            person = person_by_id.get(pid)
            if person is not None:
                if person.role == "CAST":
                    affected_cast.add(pid)
                else:
                    affected_crew.add(pid)
        affected_locations.add(scene.location_id)
        affected_equipment.update(scene.equipment_ids)
        if sid in scene_to_day:
            affected_days.add(scene_to_day[sid])

    # ── Step 4: Blast radius ──────────────────────────────────────────────────
    total_scenes = len(state.scenes)
    blast = len(all_affected) / total_scenes if total_scenes > 0 else 0.0

    return ImpactReport(
        event_id=event.event_id,
        directly_affected_scene_ids=tuple(sorted(directly_affected)),
        downstream_scene_ids=tuple(sorted(downstream)),
        affected_cast_ids=tuple(sorted(affected_cast)),
        affected_crew_ids=tuple(sorted(affected_crew)),
        affected_location_ids=tuple(sorted(affected_locations)),
        affected_equipment_ids=tuple(sorted(affected_equipment)),
        affected_days=tuple(sorted(affected_days)),
        downstream_dependency_count=len(downstream),
        blast_radius=round(blast, 6),
    )


# ---------------------------------------------------------------------------
# Frontend payload
# ---------------------------------------------------------------------------


def graph_payload(
    state: ProductionState,
    impact: ImpactReport | None = None,
) -> dict[str, object]:
    """Return a dict shaped for ``@xyflow/react`` consumption.

    Node status values:
        ``"impacted"``   — directly affected by the event
        ``"downstream"`` — blocked due to a prerequisite being impacted
        ``"ok"``         — unaffected

    Inputs:
        state:  The production state to visualise.
        impact: Optional :class:`~pri.domain.models.ImpactReport`.  When
                ``None``, all nodes get status ``"ok"``.

    Outputs:
        A ``dict`` with keys ``"nodes"`` and ``"edges"`` ready to JSON-serialise
        and pass to the React flow component.

    Failure modes:
        Does not raise; missing entities get status ``"ok"``.
    """
    g = build_graph(state)

    impacted_scenes: set[str] = set()
    downstream_scenes: set[str] = set()
    if impact is not None:
        impacted_scenes = set(impact.directly_affected_scene_ids)
        downstream_scenes = set(impact.downstream_scene_ids)

    nodes: list[dict[str, object]] = []
    for node_id, attrs in g.nodes(data=True):
        entity_id = attrs.get("entity_id", node_id)
        node_type = attrs.get("type", "unknown")
        label = attrs.get("label", node_id)

        status: str
        if node_type == _NODE_TYPE_SCENE:
            sid = str(entity_id)
            if sid in impacted_scenes:
                status = _STATUS_IMPACTED
            elif sid in downstream_scenes:
                status = _STATUS_DOWNSTREAM
            else:
                status = _STATUS_OK
        else:
            status = _STATUS_OK

        nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "label": label,
                "status": status,
            }
        )

    edges: list[dict[str, object]] = []
    for i, (src, tgt, edata) in enumerate(g.edges(data=True)):
        edges.append(
            {
                "id": f"e{i}",
                "source": src,
                "target": tgt,
                "kind": edata.get("kind", ""),
            }
        )

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _prerequisite_subgraph(g: nx.DiGraph) -> nx.DiGraph:
    """Return a subgraph containing only PREREQUISITE edges.

    Inputs:
        g: The full production dependency graph.

    Outputs:
        A new ``DiGraph`` with only nodes and edges of kind ``PREREQUISITE``.

    Failure modes:
        Does not raise; returns an empty graph if no such edges exist.
    """
    prereq_edges = [(u, v) for u, v, d in g.edges(data=True) if d.get("kind") == _KIND_PREREQUISITE]
    return g.edge_subgraph(prereq_edges).copy() if prereq_edges else nx.DiGraph()


def _scenes_in_window(
    day_to_scenes: dict[date, set[str]],
    start: datetime,
    end: datetime,
) -> set[str]:
    """Return scene IDs scheduled on any day that falls in ``[start.date, end.date)``.

    Inputs:
        day_to_scenes: Mapping of shooting date -> set of scene IDs.
        start:         Window start (inclusive, uses .date()).
        end:           Window end (exclusive, uses .date()).

    Outputs:
        Set of scene IDs whose shooting day falls in the half-open window.
    """
    result: set[str] = set()
    start_d = start.date()
    end_d = end.date()
    for d, scenes in day_to_scenes.items():
        if start_d <= d < end_d:
            result.update(scenes)
    return result


def _resolve_direct_impact(
    event: DisruptionEvent,
    state: ProductionState,
    day_to_scenes: dict[date, set[str]],
    payload: dict[str, object],
) -> set[str]:
    """Identify the set of scene IDs directly blocked by ``event``.

    Dispatch table for event_type:
        location.blocked   — scenes at that location inside the blocked window
        actor.unavailable  — scenes requiring that cast member inside the window
        equipment.failed   — scenes requiring that equipment (no window check)
        crew.unavailable   — scenes on the affected unit's days in the window
        weather.changed    — EXT scenes scheduled in the window

    Inputs:
        event:         The disruption event to resolve.
        state:         The current production state.
        day_to_scenes: Precomputed date -> scene_id mapping.
        payload:       ``event.payload`` (passed in to avoid repeated access).

    Outputs:
        Set of directly-blocked scene IDs.

    Failure modes:
        Raises ``ValueError`` for unrecognised event_type.
    """
    et = event.event_type

    if et == "location.blocked":
        return _impact_location_blocked(event, state, day_to_scenes, payload)
    if et == "actor.unavailable":
        return _impact_actor_unavailable(event, state, day_to_scenes, payload)
    if et == "equipment.failed":
        return _impact_equipment_failed(state, payload)
    if et == "crew.unavailable":
        return _impact_crew_unavailable(event, state, day_to_scenes, payload)
    if et == "weather.changed":
        return _impact_weather_changed(event, state, day_to_scenes)
    raise ValueError(f"Unrecognised event_type: {et!r}")


def _impact_location_blocked(
    event: DisruptionEvent,
    state: ProductionState,
    day_to_scenes: dict[date, set[str]],
    payload: dict[str, object],
) -> set[str]:
    """Scenes at the blocked location scheduled inside the blocked window."""
    location_id = str(payload.get("location_id", ""))
    window_start, window_end = _extract_window(payload, event.occurred_at)

    scenes_in_window = _scenes_in_window(day_to_scenes, window_start, window_end)
    scene_by_id = {s.id: s for s in state.scenes}

    return {
        sid
        for sid in scenes_in_window
        if scene_by_id.get(sid) and scene_by_id[sid].location_id == location_id
    }


def _impact_actor_unavailable(
    event: DisruptionEvent,
    state: ProductionState,
    day_to_scenes: dict[date, set[str]],
    payload: dict[str, object],
) -> set[str]:
    """Scenes requiring the unavailable actor scheduled inside the window."""
    person_id = str(payload.get("person_id", ""))
    window_start, window_end = _extract_window(payload, event.occurred_at)

    scenes_in_window = _scenes_in_window(day_to_scenes, window_start, window_end)
    scene_by_id = {s.id: s for s in state.scenes}

    return {
        sid
        for sid in scenes_in_window
        if scene_by_id.get(sid) and person_id in scene_by_id[sid].cast_ids
    }


def _impact_equipment_failed(
    state: ProductionState,
    payload: dict[str, object],
) -> set[str]:
    """All scenes requiring the failed equipment (equipment failure = permanent block)."""
    equipment_id = str(payload.get("equipment_id", ""))
    return {s.id for s in state.scenes if equipment_id in s.equipment_ids}


def _impact_crew_unavailable(
    event: DisruptionEvent,
    state: ProductionState,
    day_to_scenes: dict[date, set[str]],
    payload: dict[str, object],
) -> set[str]:
    """Scenes on the affected unit's days inside the window."""
    unit = str(payload.get("unit", "MAIN"))
    window_start, window_end = _extract_window(payload, event.occurred_at)

    affected_day_dates = {
        day.date
        for day in state.schedule.days
        if day.unit == unit and window_start.date() <= day.date < window_end.date()
    }

    result: set[str] = set()
    for d in affected_day_dates:
        result.update(day_to_scenes.get(d, set()))
    return result


def _impact_weather_changed(
    event: DisruptionEvent,
    state: ProductionState,
    day_to_scenes: dict[date, set[str]],
) -> set[str]:
    """EXT scenes scheduled in the event window."""
    window_start, window_end = _extract_window(event.payload, event.occurred_at)

    scenes_in_window = _scenes_in_window(day_to_scenes, window_start, window_end)
    scene_by_id = {s.id: s for s in state.scenes}

    return {
        sid
        for sid in scenes_in_window
        if scene_by_id.get(sid) and scene_by_id[sid].int_ext == "EXT"
    }


def _extract_window(
    payload: dict[str, object],
    fallback_start: datetime,
) -> tuple[datetime, datetime]:
    """Extract (window_start, window_end) from a payload dict.

    Expects ``payload["window_start"]`` and ``payload["window_end"]`` as
    ISO-8601 strings or ``datetime`` objects.  Falls back to
    ``(fallback_start, fallback_start + 24h)`` if absent.

    Inputs:
        payload:        Event payload dict.
        fallback_start: The event's ``occurred_at`` datetime.

    Outputs:
        A ``(start, end)`` tuple of ``datetime`` objects.

    Failure modes:
        Raises ``ValueError`` if the window strings cannot be parsed.
    """
    from datetime import timedelta

    raw_start = payload.get("window_start")
    raw_end = payload.get("window_end")

    def _parse(v: object, fallback: datetime) -> datetime:
        if v is None:
            return fallback
        if isinstance(v, datetime):
            return v
        # ISO-8601 string
        dt = datetime.fromisoformat(str(v))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt

    start = _parse(raw_start, fallback_start)
    end = _parse(raw_end, fallback_start + timedelta(days=1))
    return start, end


#: Public alias, for `simulation.projection`.
#:
#: The projection has to derive the same window the impact did, from the same
#: payload, including the fallback when the event carries no window at all.
#: Parsing it a second time somewhere else is how the blast radius and the
#: constraint end up disagreeing about which days a disruption covers.
extract_window = _extract_window


# ---------------------------------------------------------------------------
# Lazy node creation helpers (prevent KeyError for cross-reference nodes)
# ---------------------------------------------------------------------------


def _ensure_person_node(g: nx.DiGraph, person_id: str) -> None:
    """Add a stub person node if one doesn't already exist."""
    nid = f"person:{person_id}"
    if nid not in g:
        g.add_node(nid, type=_NODE_TYPE_PERSON, label=person_id, entity_id=person_id)


def _ensure_location_node(g: nx.DiGraph, location_id: str) -> None:
    """Add a stub location node if one doesn't already exist."""
    nid = f"loc:{location_id}"
    if nid not in g:
        g.add_node(nid, type=_NODE_TYPE_LOCATION, label=location_id, entity_id=location_id)


def _ensure_equipment_node(g: nx.DiGraph, equipment_id: str) -> None:
    """Add a stub equipment node if one doesn't already exist."""
    nid = f"equip:{equipment_id}"
    if nid not in g:
        g.add_node(nid, type=_NODE_TYPE_EQUIPMENT, label=equipment_id, entity_id=equipment_id)
