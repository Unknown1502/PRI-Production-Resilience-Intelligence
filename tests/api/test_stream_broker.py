"""One disruption is narrated once, and the broker is not what enforces it.

Two callers used to announce EVENT_RECEIVED: the ingest route, because the
browser is already watching when the button is pressed, and the recovery
service, because a disruption arriving over Kafka reaches no HTTP route at all.
On the interactive path both fired and the timeline read "Disruption received"
twice for one event.

Two fixes were tried in the broker and both were wrong:

  Remembering the last event id per production suppressed it forever. The
  seeded demo disruption has a fixed id, `evt-loc04-blocked`, so the second demo
  run in a process lost its opening stage — and with the API pinned to one
  instance that process lives all day, meaning the first demo of the day
  narrated correctly and every one after it started at IMPACT_COMPUTED.

  Time-boxing that memory to 30s only replaced the bug with a guess about how
  long a demo takes. `scripts/verify_live_demo.py` run twice in a row still
  failed, because the first run finished inside the window.

The fix is structural: `run_recovery` is the single announcer, and both paths
pass through it. The broker went back to being a plain fan-out with no state,
which is what these tests hold it to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pri.api.stream import Stage, StreamBroker

if TYPE_CHECKING:
    import asyncio


def drain(queue: asyncio.Queue[str]) -> list[str]:
    """Every frame waiting on a queue, as stage names."""
    stages: list[str] = []
    while not queue.empty():
        frame = queue.get_nowait()
        stages.append(frame.split("\n", 1)[0].removeprefix("event: "))
    return stages


class TestTheBrokerIsAPlainFanOut:
    """No deduplication, no memory, no clock. Publish what you are given."""

    def test_the_same_event_twice_is_delivered_twice(self) -> None:
        """The regression, stated as the property that was broken.

        A broker that swallows a repeat is a broker that decides a demo run is
        a duplicate of the one before it. Whether one event produces one row is
        the callers' business, and it is settled by having exactly one caller.
        """
        broker = StreamBroker()
        queue = broker.subscribe("film-001")

        payload = {"event_id": "evt-loc04-blocked", "event_type": "location.blocked"}
        broker.publish("film-001", Stage.EVENT_RECEIVED, payload)
        broker.publish("film-001", Stage.EVENT_RECEIVED, payload)

        assert drain(queue) == ["EVENT_RECEIVED", "EVENT_RECEIVED"]

    def test_it_keeps_no_state_between_publishes(self) -> None:
        broker = StreamBroker()
        assert not hasattr(broker, "_announced"), (
            "the broker is holding deduplication state again — that is what "
            "silently dropped the opening stage of every demo after the first"
        )

    def test_repeated_stages_are_not_collapsed(self) -> None:
        """CANDIDATE_INVALID fires once per rejected candidate.

        It is the refusal the demo is built around, and collapsing it would
        hide the second rejection in a run that produced two.
        """
        broker = StreamBroker()
        queue = broker.subscribe("film-001")

        broker.publish("film-001", Stage.CANDIDATE_INVALID, {"plan_id": "plan-B"})
        broker.publish("film-001", Stage.CANDIDATE_INVALID, {"plan_id": "plan-B"})

        assert drain(queue) == ["CANDIDATE_INVALID", "CANDIDATE_INVALID"]


class TestFanOut:
    def test_every_subscriber_gets_the_frame(self) -> None:
        """Two judges watching at once is the case the deployment is pinned for."""
        broker = StreamBroker()
        first = broker.subscribe("film-001")
        second = broker.subscribe("film-001")

        broker.publish("film-001", Stage.IMPACT_COMPUTED, {"blast_radius": 0.31})

        assert drain(first) == ["IMPACT_COMPUTED"]
        assert drain(second) == ["IMPACT_COMPUTED"]

    def test_a_frame_only_reaches_its_own_production(self) -> None:
        broker = StreamBroker()
        watching = broker.subscribe("film-001")
        elsewhere = broker.subscribe("film-002")

        broker.publish("film-001", Stage.IMPACT_COMPUTED, {})

        assert drain(watching) == ["IMPACT_COMPUTED"]
        assert drain(elsewhere) == []

    def test_unsubscribing_stops_delivery(self) -> None:
        broker = StreamBroker()
        queue = broker.subscribe("film-001")
        broker.unsubscribe("film-001", queue)

        broker.publish("film-001", Stage.IMPACT_COMPUTED, {})

        assert drain(queue) == []
        assert broker.subscriber_count("film-001") == 0


class TestOnlyRecoveryAnnouncesTheEvent:
    """The structural half of the fix, asserted where it actually lives."""

    def test_the_ingest_route_does_not_publish_event_received(self) -> None:
        """Read out of the source, because this is a fact about who calls whom.

        If someone re-adds a publish to the ingest route, the interactive path
        goes back to narrating one disruption twice — and the unit tests for
        the broker cannot see it, because the broker is doing its job either
        way.
        """
        from pathlib import Path

        source = (Path(__file__).parents[2] / "src" / "pri" / "api" / "routes.py").read_text(
            encoding="utf-8"
        )
        ingest = source[source.index("async def ingest_event") :]
        ingest = ingest[: ingest.index("\n@router.")]

        assert "Stage.EVENT_RECEIVED" not in ingest, (
            "the ingest route announces EVENT_RECEIVED again; run_recovery "
            "already does, and both firing is what produced two "
            "'Disruption received' rows for one event"
        )

    def test_recovery_still_announces_it(self) -> None:
        from pathlib import Path

        source = (
            Path(__file__).parents[2] / "src" / "pri" / "api" / "recovery_service.py"
        ).read_text(encoding="utf-8")
        assert "Stage.EVENT_RECEIVED" in source, (
            "nothing announces EVENT_RECEIVED any more — the timeline will open at IMPACT_COMPUTED"
        )
