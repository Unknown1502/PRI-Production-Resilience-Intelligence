"""Publishing to Confluent, without ever being able to fail a request.

Thin wrapper over ``confluent_kafka.Producer`` with one hard rule: **publish
never raises into a caller and never blocks longer than a few seconds.** A
disrupted event fabric must degrade the demo, not break it.

Three things make that true.

``publish`` returns a :class:`PublishResult` instead of raising. Every caller
gets an ``ok`` flag and an error string, and can decide for itself whether that
matters. Nothing has to wrap a publish in ``try``.

The client timeouts are bounded — see :func:`pri.events.config.producer_config`.
librdkafka's own defaults are measured in minutes.

A circuit breaker stops the bleeding. Without it, once the broker is
unreachable every single request pays the full delivery timeout before
degrading, and an API that answers in eight seconds reads as hung rather than
degraded. After a few consecutive failures the producer stops trying for a
cooldown and returns ``ok=False`` immediately.

``produce`` is asynchronous inside librdkafka: the call returns before the
broker has seen anything, and the delivery callback is the only place that
knows whether a message landed. So a successful ``PublishResult`` means
"accepted for delivery", and the breaker is driven by the callback as well as
by synchronous errors.

Failures log at WARNING, never ERROR. A degraded event fabric is not a server
fault and should not page anyone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

import structlog
from confluent_kafka import Producer

from pri.events.config import producer_config, topics_for

if TYPE_CHECKING:
    from types import TracebackType

    from pri.config import Settings
    from pri.events.schemas import EventEnvelope, RecoveryCompleted, RecoveryRequested

__all__ = ["ProductionEventProducer", "PublishResult"]

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PublishResult:
    """The outcome of one publish attempt.

    Inputs:
        ok:         Whether the message was accepted for delivery. False also
                    covers "the breaker is open and we did not try".
        topic:      Where it was going.
        error:      Why it failed, or ``None``.
        latency_ms: How long the attempt cost the caller. Worth watching: this
                    is time a user was waiting.
    """

    ok: bool
    topic: str
    error: str | None = None
    latency_ms: float = 0.0


class _Breaker:
    """Open after `threshold` consecutive failures; close after `cooldown`."""

    def __init__(self, threshold: int, cooldown: float) -> None:
        self._threshold = max(1, threshold)
        self._cooldown = cooldown
        self._consecutive = 0
        self._open_until = 0.0

    @property
    def is_open(self) -> bool:
        if self._open_until == 0.0:
            return False
        if time.monotonic() >= self._open_until:
            # Cooldown elapsed: half-open, so the next attempt is allowed
            # through and decides whether to re-open.
            self._open_until = 0.0
            self._consecutive = 0
            return False
        return True

    @property
    def opens_in_seconds(self) -> float:
        return max(0.0, self._open_until - time.monotonic())

    def record_failure(self) -> None:
        self._consecutive += 1
        if self._consecutive >= self._threshold and self._open_until == 0.0:
            self._open_until = time.monotonic() + self._cooldown
            _log.warning(
                "kafka_circuit_opened",
                consecutive_failures=self._consecutive,
                cooldown_seconds=self._cooldown,
            )

    def record_success(self) -> None:
        self._consecutive = 0
        self._open_until = 0.0


class ProductionEventProducer:
    """Publishes PRI messages to Confluent.

    Inputs (constructor):
        settings: Supplies bootstrap servers, credentials, topics and timeouts.
        producer: Inject a double in tests; a real ``Producer`` otherwise.

    Usage::

        with ProductionEventProducer(settings) as producer:
            result = producer.publish_event(envelope)
            if not result.ok:
                ...  # degraded, not broken
    """

    def __init__(self, settings: Settings, producer: Any | None = None) -> None:
        self._producer = producer if producer is not None else Producer(producer_config(settings))
        self._topics = topics_for(settings)
        self._breaker = _Breaker(
            settings.kafka_breaker_threshold, settings.kafka_breaker_cooldown_seconds
        )
        self._delivered = 0
        self._failed = 0
        self._last_error: str | None = None
        self._last_delivery: dict[str, Any] | None = None

    # ------------------------------------------------------------------

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.flush()

    # ------------------------------------------------------------------

    @property
    def topics(self) -> Any:
        """The resolved topic names this producer writes to."""
        return self._topics

    @property
    def delivered(self) -> int:
        """Messages the broker has acknowledged since this producer was created."""
        return self._delivered

    @property
    def failed(self) -> int:
        """Messages the broker rejected or that timed out."""
        return self._failed

    @property
    def last_delivery(self) -> dict[str, Any] | None:
        """The last broker acknowledgement: topic, partition, offset."""
        return self._last_delivery

    @property
    def degraded(self) -> bool:
        """Whether the breaker is currently refusing to attempt a publish."""
        return self._breaker.is_open

    def health(self) -> dict[str, Any]:
        """A snapshot for ``/health``. Never touches the network."""
        return {
            "delivered": self._delivered,
            "failed": self._failed,
            "degraded": self._breaker.is_open,
            "reopens_in_seconds": round(self._breaker.opens_in_seconds, 1),
            "last_error": self._last_error,
            # Positive evidence, not the absence of errors. A broker that has
            # never been reached and a topic that does not exist both produce
            # zero errors on the publish call — the acknowledgement carrying a
            # partition and an offset is the only thing that proves delivery.
            "last_delivery": self._last_delivery,
        }

    def _on_delivery(self, err: Any, msg: Any) -> None:
        """Delivery report — the only place that knows whether a publish worked."""
        if err is not None:
            self._failed += 1
            self._last_error = str(err)
            self._breaker.record_failure()
            topic = msg.topic() if msg is not None and hasattr(msg, "topic") else None
            # WARNING, not ERROR: the fabric is degraded, the service is not.
            _log.warning("kafka_delivery_failed", error=str(err), topic=topic)
            return
        self._delivered += 1
        self._breaker.record_success()
        self._last_delivery = {
            "topic": msg.topic(),
            "partition": msg.partition(),
            "offset": msg.offset(),
        }
        _log.info("kafka_delivered", **self._last_delivery)

    def publish(self, topic: str, key: str, payload: bytes) -> PublishResult:
        """Queue one message for delivery. Never raises.

        Inputs:
            topic:   Destination topic.
            key:     Partition key; use the production id so one production's
                     messages stay mutually ordered.
            payload: Serialised body.

        Outputs:
            A :class:`PublishResult`. ``ok=True`` means accepted for delivery,
            not delivered — the delivery callback settles that later and feeds
            the breaker.
        """
        if self._breaker.is_open:
            return PublishResult(
                ok=False,
                topic=topic,
                error=(
                    f"circuit open after repeated failures; retrying in "
                    f"{self._breaker.opens_in_seconds:.0f}s"
                ),
            )

        started = time.perf_counter()
        try:
            self._producer.produce(
                topic=topic,
                key=key.encode("utf-8"),
                value=payload,
                on_delivery=self._on_delivery,
            )
            # poll(0) services delivery callbacks without blocking.
            self._producer.poll(0)
        except Exception as exc:
            # BufferError means the local queue is full because the broker is
            # unreachable; KafkaException covers the rest. Neither is allowed
            # to reach a request handler.
            self._failed += 1
            self._last_error = str(exc)
            self._breaker.record_failure()
            elapsed = (time.perf_counter() - started) * 1000
            _log.warning("kafka_publish_failed", topic=topic, error=str(exc))
            return PublishResult(ok=False, topic=topic, error=str(exc), latency_ms=elapsed)

        return PublishResult(
            ok=True, topic=topic, latency_ms=(time.perf_counter() - started) * 1000
        )

    def publish_event(self, envelope: EventEnvelope) -> PublishResult:
        """Publish a disruption to the events topic."""
        return self.publish(self._topics.events, envelope.production_id, envelope.to_bytes())

    def publish_recovery_requested(self, message: RecoveryRequested) -> PublishResult:
        """Announce that recovery has started for an event."""
        return self.publish(
            self._topics.recovery_requested, message.production_id, message.to_bytes()
        )

    def publish_recovery_completed(self, message: RecoveryCompleted) -> PublishResult:
        """Announce that recovery options are ready for a human."""
        return self.publish(
            self._topics.recovery_completed, message.production_id, message.to_bytes()
        )

    def poll(self, timeout: float = 0.0) -> int:
        """Service pending delivery callbacks.

        ``produce`` only queues; librdkafka delivers on its own thread and then
        parks the callback until someone polls. Nothing calls this on a request
        path — the fire-and-forget publisher polls from its worker thread after
        the response has gone out. Without it the callbacks are never drained,
        so ``delivered`` and ``last_delivery`` stay empty even for messages the
        broker accepted, and the health page cannot tell a working integration
        from a silent one.

        Blocking, so never call it from the event loop.
        """
        try:
            served: int = self._producer.poll(timeout)
        except Exception as exc:
            _log.warning("kafka_poll_failed", error=str(exc))
            return 0
        return served

    def flush(self, timeout: float = 10.0) -> int:
        """Block until every queued message is delivered or the timeout expires.

        Only ever called on shutdown, never on a request path.

        Outputs:
            The number of messages still undelivered. Non-zero on shutdown means
            data was lost, so callers should log it rather than ignore it.
        """
        try:
            remaining: int = self._producer.flush(timeout)
        except Exception as exc:
            _log.warning("kafka_flush_failed", error=str(exc))
            return -1
        if remaining:
            _log.warning("kafka_flush_incomplete", remaining=remaining)
        return remaining
