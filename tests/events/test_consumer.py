"""Consumer behaviour, with a fake broker.

The two properties that matter are idempotency and offset discipline, and both
are about what happens when something goes wrong. They are tested against
doubles rather than a live cluster so they run in CI; the live round-trip is
in ``test_roundtrip.py`` behind the ``integration`` marker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import pytest

from pri.config import get_settings
from pri.events.config import topics_for
from pri.events.consumer import RecoveryConsumer
from pri.events.producer import ProductionEventProducer
from pri.events.schemas import EventEnvelope, RecoveryCompleted, RecoveryRequested

if TYPE_CHECKING:
    from pri.domain.models import DisruptionEvent, ProductionState


class FakeMessage:
    """One Kafka message."""

    def __init__(self, value: bytes, offset: int = 0) -> None:
        self._value = value
        self._offset = offset

    def value(self) -> bytes:
        return self._value

    def error(self) -> None:
        return None

    def topic(self) -> str:
        return topics_for(get_settings()).events

    def offset(self) -> int:
        return self._offset

    def partition(self) -> int:
        return 0


class FakeConsumer:
    """Records what was committed, so the tests can assert offset discipline."""

    def __init__(self, messages: list[FakeMessage]) -> None:
        self._messages = list(messages)
        self.committed: list[int] = []
        self.subscribed: list[str] = []
        self.closed = False

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed = topics

    def poll(self, _timeout: float) -> FakeMessage | None:
        return self._messages.pop(0) if self._messages else None

    def commit(self, message: FakeMessage, asynchronous: bool = True) -> None:
        del asynchronous
        self.committed.append(message.offset())

    def close(self) -> None:
        self.closed = True


class FakeKafkaProducer:
    """Captures produced messages instead of sending them."""

    def __init__(self) -> None:
        self.produced: list[tuple[str, bytes]] = []

    def produce(self, topic: str, key: bytes, value: bytes, on_delivery: Any) -> None:
        del key, on_delivery
        self.produced.append((topic, value))

    def poll(self, _timeout: float) -> int:
        return 0

    def flush(self, _timeout: float = 10.0) -> int:
        return 0


class FakeRepo:
    """Repository double: an in-memory event id set is the whole dedupe story."""

    def __init__(self, state: ProductionState) -> None:
        self._state = state
        self.seen: set[str] = set()

    async def record_event(self, event: DisruptionEvent) -> bool:
        if event.event_id in self.seen:
            return False
        self.seen.add(event.event_id)
        return True

    async def get_current_state(self, _production_id: str) -> ProductionState:
        return self._state


class CountingInvoker:
    """Counts how many times recovery was actually triggered."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, production_id: str, event: DisruptionEvent) -> Any:
        del production_id, event
        self.calls += 1

        class Outcome:
            session_id = "rec-test"
            candidate_count = 4
            valid_count = 3
            pareto_plan_ids: ClassVar[list[str]] = ["plan-A", "plan-B2"]
            recommended_plan_id = "plan-B2"
            failed_rule_codes: ClassVar[list[str]] = ["C001"]

        return Outcome()


class ExplodingInvoker:
    """Fails the way a database outage would."""

    async def __call__(self, production_id: str, event: DisruptionEvent) -> Any:
        del production_id, event
        raise RuntimeError("database unreachable")


def _build(
    messages: list[FakeMessage], state: ProductionState, invoker: Any
) -> tuple[RecoveryConsumer, FakeConsumer, FakeKafkaProducer, FakeRepo]:
    settings = get_settings()
    fake_consumer = FakeConsumer(messages)
    fake_kafka = FakeKafkaProducer()
    producer = ProductionEventProducer(settings, producer=fake_kafka)
    repo = FakeRepo(state)
    consumer = RecoveryConsumer(
        settings,
        repo,  # type: ignore[arg-type]
        producer,
        invoker,
        consumer=fake_consumer,
    )
    return consumer, fake_consumer, fake_kafka, repo


# ---------------------------------------------------------------------------


async def test_handles_a_disruption_end_to_end(
    night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
) -> None:
    envelope = EventEnvelope.of(loc04_blocked_event)
    invoker = CountingInvoker()
    consumer, fake_consumer, fake_kafka, _ = _build(
        [FakeMessage(envelope.to_bytes(), offset=11)], night_train_state, invoker
    )

    handled = await consumer.run(poll_timeout=0.0, max_messages=1)

    assert handled == 1
    assert invoker.calls == 1
    assert fake_consumer.committed == [11]

    topics = [topic for topic, _ in fake_kafka.produced]
    assert topics_for(get_settings()).recovery_requested in topics
    assert topics_for(get_settings()).recovery_completed in topics


async def test_the_same_event_twice_produces_one_recovery(
    night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
) -> None:
    """Redelivery after a crash must not double-plan the same disruption."""
    envelope = EventEnvelope.of(loc04_blocked_event)
    invoker = CountingInvoker()
    consumer, fake_consumer, _, _ = _build(
        [
            FakeMessage(envelope.to_bytes(), offset=1),
            FakeMessage(envelope.to_bytes(), offset=2),
        ],
        night_train_state,
        invoker,
    )

    await consumer.run(poll_timeout=0.0, max_messages=1)
    await consumer.handle(FakeMessage(envelope.to_bytes(), offset=2))

    assert invoker.calls == 1
    assert consumer.skipped == 1
    # Both offsets commit: the duplicate is handled, just not re-planned.
    assert fake_consumer.committed == [1, 2]


async def test_a_failed_handler_does_not_commit_and_re_raises(
    night_train_state: ProductionState, loc04_blocked_event: DisruptionEvent
) -> None:
    """The offset must stay put so the broker redelivers."""
    envelope = EventEnvelope.of(loc04_blocked_event)
    consumer, fake_consumer, _, _ = _build(
        [FakeMessage(envelope.to_bytes(), offset=7)],
        night_train_state,
        ExplodingInvoker(),
    )

    with pytest.raises(RuntimeError, match="database unreachable"):
        await consumer.run(poll_timeout=0.0, max_messages=1)

    assert fake_consumer.committed == []
    assert consumer.handled == 0


async def test_an_undecodable_message_is_skipped_not_retried_forever(
    night_train_state: ProductionState,
) -> None:
    """A foreign message must not be able to wedge the topic.

    Found by running the consumer against the real cluster: a smoke-test
    message sat on `production.events`, the consumer re-raised on every
    delivery, and it crashlooped six times in a minute without ever reaching
    the messages behind it. Bytes that are not an envelope will never become
    one, so committing and moving on is the only outcome that lets the queue
    drain. The retry half of the rule is covered by
    `test_a_failed_handler_does_not_commit_and_re_raises` above.
    """
    message = FakeMessage(b"not json at all", offset=3)
    consumer, fake_consumer, _, _ = _build([message], night_train_state, CountingInvoker())

    # handle() directly rather than run(): `max_messages` counts *handled*
    # messages, and a skipped one is deliberately not handled, so the poll loop
    # would spin forever waiting for a count that never rises.
    await consumer.handle(message)

    assert consumer.undecodable == 1
    assert consumer.handled == 0, "a skipped message is not a handled one"
    assert fake_consumer.committed == [3], "the offset must advance or the topic stalls"


def test_subscribe_joins_the_events_topic(night_train_state: ProductionState) -> None:
    consumer, fake_consumer, _, _ = _build([], night_train_state, CountingInvoker())
    consumer.subscribe()
    # Read from settings, not restated: the point of the change that broke this
    # assertion was that a hardcoded topic name can disagree with the deployed one.
    assert fake_consumer.subscribed == [topics_for(get_settings()).events]


# ---------------------------------------------------------------------------
# Envelope round-trip
# ---------------------------------------------------------------------------


def test_envelope_round_trips(loc04_blocked_event: DisruptionEvent) -> None:
    envelope = EventEnvelope.of(loc04_blocked_event)
    restored = EventEnvelope.from_bytes(envelope.to_bytes())
    assert restored.event.event_id == loc04_blocked_event.event_id
    assert restored.event.payload == loc04_blocked_event.payload
    assert restored.schema_version == 1
    assert restored.key == "film-001"


def test_recovery_messages_carry_the_rule_codes() -> None:
    completed = RecoveryCompleted(
        production_id="film-001",
        event_id="evt-1",
        session_id="rec-1",
        candidate_count=4,
        valid_count=3,
        pareto_plan_ids=["plan-A", "plan-B2"],
        recommended_plan_id="plan-B2",
        failed_rule_codes=["C001"],
    )
    restored = RecoveryCompleted.from_bytes(completed.to_bytes())
    assert restored.failed_rule_codes == ["C001"]
    assert restored.kind == "recovery.completed"

    requested = RecoveryRequested(production_id="film-001", event_id="evt-1", base_version=1)
    assert RecoveryRequested.from_bytes(requested.to_bytes()).base_version == 1
