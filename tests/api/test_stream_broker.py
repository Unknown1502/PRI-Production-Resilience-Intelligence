"""The EVENT_RECEIVED deduplication, and the bug it caused.

One disruption must be narrated once. Two callers legitimately announce it —
the ingest route, because the browser is already watching when the button is
pressed, and the recovery service, because a disruption arriving over Kafka
reaches no HTTP route at all — so the interactive path published it twice and
the timeline read "Disruption received" twice for one event.

The first fix remembered the last event id per production forever. That was
wrong in a way no unit test caught and the deployment made permanent: the
seeded demo disruption has a fixed id, `evt-loc04-blocked`, so the second demo
run in a process was deduplicated against the first and silently lost its
opening stage. With the API pinned to one instance the process lives all day,
which means the first demo of the day narrated correctly and every one after it
did not. `scripts/verify_live_demo.py` found it on its second run.

So the window is time-boxed, and both halves of that need holding down: it must
still suppress the double-publish one user action produces, and it must not
suppress the same event id an hour later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pri.api.stream import Stage, StreamBroker

if TYPE_CHECKING:
    import asyncio

    import pytest


def drain(queue: asyncio.Queue[str]) -> list[str]:
    """Every frame waiting on a queue, as stage names."""
    stages: list[str] = []
    while not queue.empty():
        frame = queue.get_nowait()
        stages.append(frame.split("\n", 1)[0].removeprefix("event: "))
    return stages


class TestOneDisruptionIsNarratedOnce:
    def test_the_same_event_twice_in_one_flow_announces_once(self) -> None:
        """Ingest publishes, then recovery publishes, under a second apart."""
        broker = StreamBroker()
        queue = broker.subscribe("film-001")

        payload = {"event_id": "evt-loc04-blocked", "event_type": "location.blocked"}
        broker.publish("film-001", Stage.EVENT_RECEIVED, payload)
        broker.publish("film-001", Stage.EVENT_RECEIVED, payload)

        assert drain(queue) == ["EVENT_RECEIVED"]

    def test_two_different_events_both_announce(self) -> None:
        broker = StreamBroker()
        queue = broker.subscribe("film-001")

        broker.publish("film-001", Stage.EVENT_RECEIVED, {"event_id": "evt-one"})
        broker.publish("film-001", Stage.EVENT_RECEIVED, {"event_id": "evt-two"})

        assert drain(queue) == ["EVENT_RECEIVED", "EVENT_RECEIVED"]

    def test_the_same_event_in_two_productions_both_announce(self) -> None:
        """Deduplication is per production, not global."""
        broker = StreamBroker()
        first = broker.subscribe("film-001")
        second = broker.subscribe("film-002")

        payload = {"event_id": "evt-loc04-blocked"}
        broker.publish("film-001", Stage.EVENT_RECEIVED, payload)
        broker.publish("film-002", Stage.EVENT_RECEIVED, payload)

        assert drain(first) == ["EVENT_RECEIVED"]
        assert drain(second) == ["EVENT_RECEIVED"]


class TestTheWindowExpires:
    """The regression. A demo run must not silence the next demo run."""

    def test_the_same_event_id_announces_again_after_the_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        broker = StreamBroker()
        queue = broker.subscribe("film-001")
        payload = {"event_id": "evt-loc04-blocked"}

        clock = [1000.0]
        monkeypatch.setattr("pri.api.stream.time.monotonic", lambda: clock[0])

        broker.publish("film-001", Stage.EVENT_RECEIVED, payload)
        broker.publish("film-001", Stage.EVENT_RECEIVED, payload)  # same flow, suppressed
        assert drain(queue) == ["EVENT_RECEIVED"]

        # The next demo run, minutes later, with the same seeded event id.
        clock[0] += 600.0
        broker.publish("film-001", Stage.EVENT_RECEIVED, payload)
        assert drain(queue) == ["EVENT_RECEIVED"], (
            "the second demo run lost its opening stage — this is the bug that "
            "made every demo after the first one start at IMPACT_COMPUTED"
        )

    def test_an_event_without_an_id_is_never_suppressed(self) -> None:
        """Nothing to deduplicate on means publish it."""
        broker = StreamBroker()
        queue = broker.subscribe("film-001")

        broker.publish("film-001", Stage.EVENT_RECEIVED, {})
        broker.publish("film-001", Stage.EVENT_RECEIVED, {})

        assert drain(queue) == ["EVENT_RECEIVED", "EVENT_RECEIVED"]


class TestEveryOtherStageIsUnaffected:
    def test_repeated_stages_are_not_deduplicated(self) -> None:
        """Only EVENT_RECEIVED is deduplicated.

        CANDIDATE_INVALID in particular fires once per rejected candidate and
        must never be collapsed — it is the refusal the demo is built around.
        """
        broker = StreamBroker()
        queue = broker.subscribe("film-001")

        broker.publish("film-001", Stage.CANDIDATE_INVALID, {"plan_id": "plan-B"})
        broker.publish("film-001", Stage.CANDIDATE_INVALID, {"plan_id": "plan-B"})

        assert drain(queue) == ["CANDIDATE_INVALID", "CANDIDATE_INVALID"]

    def test_every_subscriber_gets_the_frame(self) -> None:
        """Two judges watching at once is the case the deployment is pinned for."""
        broker = StreamBroker()
        first = broker.subscribe("film-001")
        second = broker.subscribe("film-001")

        broker.publish("film-001", Stage.IMPACT_COMPUTED, {"blast_radius": 0.31})

        assert drain(first) == ["IMPACT_COMPUTED"]
        assert drain(second) == ["IMPACT_COMPUTED"]
