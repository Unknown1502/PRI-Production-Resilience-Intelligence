"""Tests for src/pri/engine/graph/dependency.py.

Seed fixture:
    4 scenes, 2 locations, 3 people (2 cast + 1 crew), 1 equipment piece.
    Schedule: D1 (sc-1, sc-2 at LOC-01), D2 (sc-3 at LOC-04), D3 (sc-4 at LOC-01).
    Prerequisite chain: sc-1 -> sc-3 -> sc-4  (sc-1 must shoot before sc-3, sc-3 before sc-4)

    Blocking LOC-04 on D2 must directly affect sc-3 (it is INT at LOC-04 on D2).
    sc-4 depends on sc-3 via PREREQUISITE, so it is downstream.
    sc-1 is a prerequisite OF sc-3 but NOT downstream — it shoots before sc-3.
    sc-2 is unrelated.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import networkx as nx
import pytest
from pydantic import ValidationError

from pri.domain.models import (
    DisruptionEvent,
    Equipment,
    Location,
    Person,
    Production,
    ProductionState,
    Scene,
    Schedule,
    ShootingDay,
)
from pri.engine.graph.dependency import (
    _KIND_PREREQUISITE,
    _KIND_REQUIRES_CAST,
    _KIND_REQUIRES_EQUIPMENT,
    _KIND_REQUIRES_LOCATION,
    _KIND_SCHEDULED_ON,
    build_graph,
    graph_payload,
    impact_of,
)

# ---------------------------------------------------------------------------
# Dates / times
# ---------------------------------------------------------------------------

D1 = date(2025, 5, 1)
D2 = date(2025, 5, 2)
D3 = date(2025, 5, 3)
D4 = date(2025, 5, 4)


def _dt(d: date, hour: int = 7) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Seed fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def seed_state() -> ProductionState:
    """Rich production state used by all impact tests."""
    # People
    cast1 = Person(
        id="cast-01",
        name="Alice",
        role="CAST",
        character="Hero",
        daily_rate=Decimal("5000"),
        unavailable_windows=(),
    )
    cast2 = Person(
        id="cast-02",
        name="Bob",
        role="CAST",
        character="Villain",
        daily_rate=Decimal("4000"),
        unavailable_windows=(),
    )
    crew1 = Person(
        id="crew-01",
        name="Charlie",
        role="CREW",
        character=None,
        daily_rate=Decimal("1500"),
        unavailable_windows=(),
    )

    # Locations
    loc01 = Location(
        id="LOC-01",
        name="Studio A",
        kind="studio",
        day_rate=Decimal("2000"),
        permit_windows=(),
        supports_int_ext=("INT",),
        supports_time_of_day=("DAY",),
    )
    loc04 = Location(
        id="LOC-04",
        name="Outdoor Park",
        kind="exterior",
        day_rate=Decimal("500"),
        permit_windows=(),
        supports_int_ext=("EXT",),
        supports_time_of_day=("DAY", "DAWN", "DUSK"),
    )

    # Equipment
    crane = Equipment(
        id="equip-01",
        name="Crane",
        kind="camera_support",
        daily_rate=Decimal("800"),
        available_windows=(),
    )

    # Scenes
    # sc-1: INT at LOC-01 on D1, requires cast-01, crew-01
    sc1 = Scene(
        id="sc-1",
        number=1,
        slug="sc001",
        description="Opening interior",
        int_ext="INT",
        time_of_day="DAY",
        estimated_minutes=45,
        location_id="LOC-01",
        cast_ids=("cast-01",),
        equipment_ids=("equip-01",),
        vfx_plate=False,
        prerequisite_scene_ids=(),
    )
    # sc-2: INT at LOC-01 on D1, requires cast-02
    sc2 = Scene(
        id="sc-2",
        number=2,
        slug="sc002",
        description="Interior dialogue",
        int_ext="INT",
        time_of_day="DAY",
        estimated_minutes=30,
        location_id="LOC-01",
        cast_ids=("cast-02",),
        equipment_ids=(),
        vfx_plate=False,
        prerequisite_scene_ids=(),
    )
    # sc-3: INT at LOC-04 on D2, requires cast-01; prereq: sc-1
    sc3 = Scene(
        id="sc-3",
        number=3,
        slug="sc003",
        description="Park confrontation",
        int_ext="INT",
        time_of_day="DAY",
        estimated_minutes=60,
        location_id="LOC-04",
        cast_ids=("cast-01",),
        equipment_ids=("equip-01",),
        vfx_plate=False,
        prerequisite_scene_ids=("sc-1",),  # sc-1 must shoot first
    )
    # sc-4: EXT at LOC-04 on D3, requires cast-01 + cast-02; prereq: sc-3
    sc4 = Scene(
        id="sc-4",
        number=4,
        slug="sc004",
        description="Outdoor chase",
        int_ext="EXT",
        time_of_day="DAY",
        estimated_minutes=90,
        location_id="LOC-04",
        cast_ids=("cast-01", "cast-02"),
        equipment_ids=(),
        vfx_plate=True,
        prerequisite_scene_ids=("sc-3",),  # sc-3 must shoot first
    )

    # Schedule
    day1 = ShootingDay(
        date=D1,
        call_time=_dt(D1, 7),
        wrap_time=_dt(D1, 19),
        location_id="LOC-01",
        scene_ids=("sc-1", "sc-2"),
    )
    day2 = ShootingDay(
        date=D2,
        call_time=_dt(D2, 7),
        wrap_time=_dt(D2, 19),
        location_id="LOC-04",
        scene_ids=("sc-3",),
    )
    day3 = ShootingDay(
        date=D3,
        call_time=_dt(D3, 7),
        wrap_time=_dt(D3, 19),
        location_id="LOC-04",
        scene_ids=("sc-4",),
    )

    return ProductionState(
        production=Production(
            id="prod-seed",
            title="Seed Film",
            currency="USD",
            shoot_start=D1,
            shoot_end=D3,
            reserve_days=(),
        ),
        scenes=(sc1, sc2, sc3, sc4),
        people=(cast1, cast2, crew1),
        locations=(loc01, loc04),
        equipment=(crane,),
        schedule=Schedule(days=(day1, day2, day3)),
        version=1,
        parent_version=None,
        event_id=None,
        created_at=datetime(2025, 4, 1, tzinfo=UTC),
    )


def _loc04_blocked_event(state: ProductionState) -> DisruptionEvent:
    """LOC-04 blocked all day on D2 (covers sc-3's shooting day exactly)."""
    return DisruptionEvent(
        event_id="evt-loc04",
        production_id=state.production.id,
        event_type="location.blocked",
        occurred_at=datetime(2025, 5, 1, 18, 0, tzinfo=UTC),
        source="location manager",
        severity=0.9,
        payload={
            "location_id": "LOC-04",
            "window_start": "2025-05-02T00:00:00+00:00",
            "window_end": "2025-05-03T00:00:00+00:00",
        },
    )


# ---------------------------------------------------------------------------
# build_graph tests
# ---------------------------------------------------------------------------


class TestBuildGraph:
    def test_all_entity_nodes_present(self, seed_state: ProductionState) -> None:
        g = build_graph(seed_state)
        assert "scene:sc-1" in g
        assert "scene:sc-2" in g
        assert "scene:sc-3" in g
        assert "scene:sc-4" in g
        assert "person:cast-01" in g
        assert "person:cast-02" in g
        assert "person:crew-01" in g
        assert "loc:LOC-01" in g
        assert "loc:LOC-04" in g
        assert "equip:equip-01" in g
        assert "day:2025-05-01" in g
        assert "day:2025-05-02" in g
        assert "day:2025-05-03" in g

    def test_node_types_correct(self, seed_state: ProductionState) -> None:
        g = build_graph(seed_state)
        assert g.nodes["scene:sc-1"]["type"] == "scene"
        assert g.nodes["person:cast-01"]["type"] == "person"
        assert g.nodes["loc:LOC-04"]["type"] == "location"
        assert g.nodes["equip:equip-01"]["type"] == "equipment"
        assert g.nodes["day:2025-05-01"]["type"] == "shooting_day"

    def test_requires_cast_edge(self, seed_state: ProductionState) -> None:
        g = build_graph(seed_state)
        assert g.has_edge("scene:sc-1", "person:cast-01")
        assert g["scene:sc-1"]["person:cast-01"]["kind"] == _KIND_REQUIRES_CAST

    def test_requires_crew_edge(self, seed_state: ProductionState) -> None:
        # crew-01 is in cast_ids of sc-1 — but crew-01's role is CREW.
        # crew-01 is NOT in sc-1's cast_ids in the fixture (only cast-01 is).
        # crew-01 has no edges because no scene lists them in cast_ids.
        g = build_graph(seed_state)
        # Verify crew-01 node exists but has no scene->person incoming edges
        assert "person:crew-01" in g
        incoming_to_crew = [(u, v) for u, v, d in g.in_edges("person:crew-01", data=True)]
        assert len(incoming_to_crew) == 0

    def test_requires_location_edge(self, seed_state: ProductionState) -> None:
        g = build_graph(seed_state)
        assert g.has_edge("scene:sc-3", "loc:LOC-04")
        assert g["scene:sc-3"]["loc:LOC-04"]["kind"] == _KIND_REQUIRES_LOCATION

    def test_requires_equipment_edge(self, seed_state: ProductionState) -> None:
        g = build_graph(seed_state)
        assert g.has_edge("scene:sc-1", "equip:equip-01")
        assert g["scene:sc-1"]["equip:equip-01"]["kind"] == _KIND_REQUIRES_EQUIPMENT

    def test_prerequisite_edges(self, seed_state: ProductionState) -> None:
        g = build_graph(seed_state)
        # sc-1 -> sc-3  (sc-1 must be done before sc-3 can shoot)
        assert g.has_edge("scene:sc-1", "scene:sc-3")
        assert g["scene:sc-1"]["scene:sc-3"]["kind"] == _KIND_PREREQUISITE
        # sc-3 -> sc-4
        assert g.has_edge("scene:sc-3", "scene:sc-4")
        assert g["scene:sc-3"]["scene:sc-4"]["kind"] == _KIND_PREREQUISITE

    def test_scheduled_on_edges(self, seed_state: ProductionState) -> None:
        g = build_graph(seed_state)
        assert g.has_edge("day:2025-05-01", "scene:sc-1")
        assert g.has_edge("day:2025-05-01", "scene:sc-2")
        assert g.has_edge("day:2025-05-02", "scene:sc-3")
        assert g.has_edge("day:2025-05-03", "scene:sc-4")
        assert g["day:2025-05-01"]["scene:sc-1"]["kind"] == _KIND_SCHEDULED_ON

    def test_graph_is_directed(self, seed_state: ProductionState) -> None:
        g = build_graph(seed_state)
        assert isinstance(g, nx.DiGraph)

    def test_build_graph_is_pure(self, seed_state: ProductionState) -> None:
        """Calling build_graph twice returns equal graphs; state is not mutated."""
        g1 = build_graph(seed_state)
        g2 = build_graph(seed_state)
        assert set(g1.nodes) == set(g2.nodes)
        assert set(g1.edges) == set(g2.edges)
        assert seed_state.version == 1  # state untouched


# ---------------------------------------------------------------------------
# impact_of — location.blocked
# ---------------------------------------------------------------------------


class TestImpactLocationBlocked:
    def test_direct_impact_is_sc3_only(self, seed_state: ProductionState) -> None:
        """LOC-04 blocked on D2 → sc-3 directly affected (sc-4 is on D3)."""
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        assert set(report.directly_affected_scene_ids) == {"sc-3"}

    def test_downstream_is_sc4(self, seed_state: ProductionState) -> None:
        """sc-4 depends on sc-3 via PREREQUISITE → downstream."""
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        assert set(report.downstream_scene_ids) == {"sc-4"}

    def test_sc1_sc2_not_affected(self, seed_state: ProductionState) -> None:
        """sc-1 is a PREREQUISITE of sc-3 (ancestor), not a descendant → not downstream."""
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        assert "sc-1" not in report.directly_affected_scene_ids
        assert "sc-1" not in report.downstream_scene_ids
        assert "sc-2" not in report.directly_affected_scene_ids
        assert "sc-2" not in report.downstream_scene_ids

    def test_affected_cast(self, seed_state: ProductionState) -> None:
        """cast-01 is in sc-3 (direct) and sc-4 (downstream)."""
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        # cast-01 is in sc-3 and sc-4
        assert "cast-01" in report.affected_cast_ids
        # cast-02 is in sc-4 (downstream) → also affected
        assert "cast-02" in report.affected_cast_ids

    def test_affected_locations(self, seed_state: ProductionState) -> None:
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        assert "LOC-04" in report.affected_location_ids

    def test_affected_days(self, seed_state: ProductionState) -> None:
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        # D2 (sc-3 direct) and D3 (sc-4 downstream)
        assert D2 in report.affected_days
        assert D3 in report.affected_days

    def test_blast_radius(self, seed_state: ProductionState) -> None:
        """2 of 4 scenes affected → blast_radius = 0.5."""
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        assert report.blast_radius == pytest.approx(0.5)

    def test_downstream_dependency_count(self, seed_state: ProductionState) -> None:
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        assert report.downstream_dependency_count == 1

    def test_event_id_propagated(self, seed_state: ProductionState) -> None:
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        assert report.event_id == "evt-loc04"

    def test_report_is_frozen(self, seed_state: ProductionState) -> None:
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        with pytest.raises((ValidationError, TypeError)):
            report.blast_radius = 0.0  # type: ignore[misc]  # frozen → raises


# ---------------------------------------------------------------------------
# impact_of — actor.unavailable
# ---------------------------------------------------------------------------


class TestImpactActorUnavailable:
    def _actor_event(self, state: ProductionState) -> DisruptionEvent:
        """cast-01 unavailable on D1 — affects sc-1 directly, sc-3 and sc-4 downstream."""
        return DisruptionEvent(
            event_id="evt-actor",
            production_id=state.production.id,
            event_type="actor.unavailable",
            occurred_at=datetime(2025, 4, 30, tzinfo=UTC),
            source="casting office",
            severity=0.8,
            payload={
                "person_id": "cast-01",
                "window_start": "2025-05-01T00:00:00+00:00",
                "window_end": "2025-05-02T00:00:00+00:00",
            },
        )

    def test_sc1_directly_affected(self, seed_state: ProductionState) -> None:
        event = self._actor_event(seed_state)
        report = impact_of(seed_state, event)
        assert "sc-1" in report.directly_affected_scene_ids

    def test_sc3_downstream_via_prerequisite(self, seed_state: ProductionState) -> None:
        """sc-1 -> sc-3 (PREREQUISITE), so sc-3 is downstream of sc-1."""
        event = self._actor_event(seed_state)
        report = impact_of(seed_state, event)
        assert "sc-3" in report.downstream_scene_ids

    def test_sc4_downstream_transitively(self, seed_state: ProductionState) -> None:
        """sc-3 -> sc-4 (PREREQUISITE), sc-3 downstream → sc-4 also downstream."""
        event = self._actor_event(seed_state)
        report = impact_of(seed_state, event)
        assert "sc-4" in report.downstream_scene_ids

    def test_sc2_not_affected(self, seed_state: ProductionState) -> None:
        event = self._actor_event(seed_state)
        report = impact_of(seed_state, event)
        assert "sc-2" not in report.directly_affected_scene_ids
        assert "sc-2" not in report.downstream_scene_ids


# ---------------------------------------------------------------------------
# impact_of — equipment.failed
# ---------------------------------------------------------------------------


class TestImpactEquipmentFailed:
    def _equip_event(self, state: ProductionState) -> DisruptionEvent:
        return DisruptionEvent(
            event_id="evt-equip",
            production_id=state.production.id,
            event_type="equipment.failed",
            occurred_at=datetime(2025, 5, 1, 6, 0, tzinfo=UTC),
            source="equipment manager",
            severity=0.7,
            payload={"equipment_id": "equip-01"},
        )

    def test_scenes_with_equipment_affected(self, seed_state: ProductionState) -> None:
        event = self._equip_event(seed_state)
        report = impact_of(seed_state, event)
        # sc-1 and sc-3 use equip-01
        assert "sc-1" in report.directly_affected_scene_ids
        assert "sc-3" in report.directly_affected_scene_ids

    def test_scenes_without_equipment_not_direct(self, seed_state: ProductionState) -> None:
        event = self._equip_event(seed_state)
        report = impact_of(seed_state, event)
        assert "sc-2" not in report.directly_affected_scene_ids

    def test_equipment_id_in_report(self, seed_state: ProductionState) -> None:
        event = self._equip_event(seed_state)
        report = impact_of(seed_state, event)
        assert "equip-01" in report.affected_equipment_ids


# ---------------------------------------------------------------------------
# impact_of — weather.changed
# ---------------------------------------------------------------------------


class TestImpactWeatherChanged:
    def _weather_event(self, state: ProductionState) -> DisruptionEvent:
        """Bad weather on D3 — only sc-4 (EXT) is on that day."""
        return DisruptionEvent(
            event_id="evt-weather",
            production_id=state.production.id,
            event_type="weather.changed",
            occurred_at=datetime(2025, 5, 2, 20, 0, tzinfo=UTC),
            source="weather API",
            severity=0.5,
            payload={
                "window_start": "2025-05-03T00:00:00+00:00",
                "window_end": "2025-05-04T00:00:00+00:00",
            },
        )

    def test_ext_scene_affected(self, seed_state: ProductionState) -> None:
        event = self._weather_event(seed_state)
        report = impact_of(seed_state, event)
        # sc-4 is EXT on D3
        assert "sc-4" in report.directly_affected_scene_ids

    def test_int_scenes_not_directly_affected(self, seed_state: ProductionState) -> None:
        event = self._weather_event(seed_state)
        report = impact_of(seed_state, event)
        assert "sc-1" not in report.directly_affected_scene_ids
        assert "sc-2" not in report.directly_affected_scene_ids
        assert "sc-3" not in report.directly_affected_scene_ids


# ---------------------------------------------------------------------------
# impact_of — crew.unavailable
# ---------------------------------------------------------------------------


class TestImpactCrewUnavailable:
    def _crew_event(self, state: ProductionState) -> DisruptionEvent:
        """MAIN unit crew unavailable on D2."""
        return DisruptionEvent(
            event_id="evt-crew",
            production_id=state.production.id,
            event_type="crew.unavailable",
            occurred_at=datetime(2025, 5, 1, 18, 0, tzinfo=UTC),
            source="union rep",
            severity=0.6,
            payload={
                "unit": "MAIN",
                "window_start": "2025-05-02T00:00:00+00:00",
                "window_end": "2025-05-03T00:00:00+00:00",
            },
        )

    def test_scenes_on_affected_day_directly_impacted(self, seed_state: ProductionState) -> None:
        event = self._crew_event(seed_state)
        report = impact_of(seed_state, event)
        assert "sc-3" in report.directly_affected_scene_ids

    def test_scenes_on_other_days_not_directly_impacted(self, seed_state: ProductionState) -> None:
        event = self._crew_event(seed_state)
        report = impact_of(seed_state, event)
        assert "sc-1" not in report.directly_affected_scene_ids
        assert "sc-2" not in report.directly_affected_scene_ids


# ---------------------------------------------------------------------------
# impact_of — determinism and purity
# ---------------------------------------------------------------------------


class TestImpactPurity:
    def test_deterministic_same_result_twice(self, seed_state: ProductionState) -> None:
        event = _loc04_blocked_event(seed_state)
        r1 = impact_of(seed_state, event)
        r2 = impact_of(seed_state, event)
        assert r1 == r2

    def test_state_not_mutated(self, seed_state: ProductionState) -> None:
        original_version = seed_state.version
        original_scenes = seed_state.scenes
        event = _loc04_blocked_event(seed_state)
        _ = impact_of(seed_state, event)
        assert seed_state.version == original_version
        assert seed_state.scenes is original_scenes

    def test_no_scenes_gives_zero_blast_radius(self) -> None:
        """Edge case: empty schedule + zero scenes → blast_radius = 0.0."""
        from pri.domain.models import Production

        empty_state = ProductionState(
            production=Production(
                id="p0",
                title="Empty",
                currency="USD",
                shoot_start=D1,
                shoot_end=D1,
                reserve_days=(),
            ),
            scenes=(),
            people=(),
            locations=(),
            equipment=(),
            schedule=Schedule(days=()),
            version=1,
            parent_version=None,
            event_id=None,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        event = DisruptionEvent(
            event_id="evt-empty",
            production_id="p0",
            event_type="weather.changed",
            occurred_at=datetime(2025, 5, 1, tzinfo=UTC),
            source="test",
            severity=0.1,
            payload={},
        )
        report = impact_of(empty_state, event)
        assert report.blast_radius == 0.0
        assert report.directly_affected_scene_ids == ()


# ---------------------------------------------------------------------------
# graph_payload tests
# ---------------------------------------------------------------------------


class TestGraphPayload:
    def test_payload_has_nodes_and_edges_keys(self, seed_state: ProductionState) -> None:
        payload = graph_payload(seed_state)
        assert "nodes" in payload
        assert "edges" in payload

    def test_all_nodes_present_in_payload(self, seed_state: ProductionState) -> None:
        payload = graph_payload(seed_state)
        node_ids = {n["id"] for n in payload["nodes"]}  # type: ignore[index]
        assert "scene:sc-1" in node_ids
        assert "loc:LOC-04" in node_ids
        assert "equip:equip-01" in node_ids

    def test_status_ok_when_no_impact(self, seed_state: ProductionState) -> None:
        payload = graph_payload(seed_state, impact=None)
        nodes = payload["nodes"]  # type: ignore[index]
        for node in nodes:  # type: ignore[union-attr]
            assert node["status"] == "ok"

    def test_impacted_status_set(self, seed_state: ProductionState) -> None:
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        payload = graph_payload(seed_state, impact=report)
        nodes_by_id = {n["id"]: n for n in payload["nodes"]}  # type: ignore[index]
        assert nodes_by_id["scene:sc-3"]["status"] == "impacted"

    def test_downstream_status_set(self, seed_state: ProductionState) -> None:
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        payload = graph_payload(seed_state, impact=report)
        nodes_by_id = {n["id"]: n for n in payload["nodes"]}  # type: ignore[index]
        assert nodes_by_id["scene:sc-4"]["status"] == "downstream"

    def test_unaffected_scene_ok(self, seed_state: ProductionState) -> None:
        event = _loc04_blocked_event(seed_state)
        report = impact_of(seed_state, event)
        payload = graph_payload(seed_state, impact=report)
        nodes_by_id = {n["id"]: n for n in payload["nodes"]}  # type: ignore[index]
        assert nodes_by_id["scene:sc-1"]["status"] == "ok"
        assert nodes_by_id["scene:sc-2"]["status"] == "ok"

    def test_edges_have_required_fields(self, seed_state: ProductionState) -> None:
        payload = graph_payload(seed_state)
        for edge in payload["edges"]:  # type: ignore[union-attr]
            assert "id" in edge
            assert "source" in edge
            assert "target" in edge
            assert "kind" in edge
