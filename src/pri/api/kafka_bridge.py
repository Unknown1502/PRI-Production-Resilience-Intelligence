"""The API's connection to Confluent — genuinely used, never load-bearing.

Before this module the API service never touched Kafka. The producer existed
and was real, but only the consumer service ever constructed one, so a
disruption reported through the UI reached Postgres and the SSE stream and
nothing else. Kafka only participated when an event arrived *via* Kafka, which
is the one path a judge clicking a button never takes.

So the API publishes too. The rule is that it can never cost a user anything:

* Publishing is scheduled as a background task and the handler does not await
  it. The HTTP response does not wait for a broker.
* The publish itself cannot raise — :class:`~pri.events.producer.PublishResult`
  carries the failure instead — and the producer's circuit breaker means a dead
  broker costs one timeout, not one per request.
* The work runs in a worker thread. librdkafka's ``produce`` is normally
  sub-millisecond, but it can block when the local queue is full, and the event
  loop is serving everyone.

Connectivity is probed on an interval by :meth:`KafkaBridge.probe_forever`,
never inside a request. A ``/health`` handler that blocks on a broker timeout
fails Cloud Run's probe and takes down a service that was working fine.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, Literal

import structlog

from pri.events.config import topics_for
from pri.events.producer import ProductionEventProducer
from pri.events.schemas import EventEnvelope

if TYPE_CHECKING:
    from pri.config import Settings
    from pri.domain.models import DisruptionEvent

__all__ = ["KafkaBridge", "KafkaStatus"]

_log = structlog.get_logger(__name__)

KafkaStatus = Literal["ok", "degraded", "disabled"]

#: How often the background task asks the broker for metadata.
_PROBE_INTERVAL_SECONDS = 30.0

#: How long that request may take before it counts as a failure. Well under
#: the interval, so probes cannot pile up.
_PROBE_TIMEOUT_SECONDS = 5.0

#: How long a background publish waits for the broker's acknowledgement. Off
#: the request path entirely, so it can afford to be patient.
_DELIVERY_POLL_SECONDS = 10.0


class KafkaBridge:
    """Publishes API-side events to Confluent without blocking a request.

    Inputs (constructor):
        settings: Supplies credentials, topics and timeouts.
        producer: Inject a double in tests.

    When Confluent is not configured the bridge is *disabled*: every publish is
    a no-op that reports ``disabled`` rather than ``degraded``, because a local
    developer with no Kafka credentials has not broken anything.
    """

    def __init__(self, settings: Settings, producer: Any | None = None) -> None:
        self._settings = settings
        self._enabled = settings.confluent_configured or producer is not None
        self._producer: ProductionEventProducer | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._probe_task: asyncio.Task[None] | None = None
        self._reachable: bool | None = None
        self._probe_error: str | None = None

        if self._enabled:
            try:
                self._producer = ProductionEventProducer(settings, producer=producer)
            except Exception as exc:
                # Constructing a Producer can fail on a malformed config. That
                # must not stop the API from starting.
                _log.warning("kafka_bridge_unavailable", error=str(exc))
                self._enabled = False
                self._probe_error = str(exc)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled and self._producer is not None

    def publish_disruption(self, event: DisruptionEvent) -> None:
        """Announce a disruption. Returns immediately; never raises."""
        if not self.enabled:
            return
        self._schedule(EventEnvelope.of(event))

    def _schedule(self, envelope: EventEnvelope) -> None:
        producer = self._producer
        if producer is None:
            return

        async def run() -> None:
            result = await asyncio.to_thread(producer.publish_event, envelope)
            if not result.ok:
                _log.warning(
                    "kafka_publish_degraded",
                    topic=result.topic,
                    error=result.error,
                    production_id=envelope.production_id,
                )
                return
            # Drain the delivery callback. `produce` only queues; the broker's
            # acknowledgement arrives on librdkafka's own thread and waits for
            # a poll. Skipping this leaves `delivered` at zero for messages
            # that landed perfectly — a health page that cannot tell a working
            # integration from a silent one is worse than no health page.
            # Safe to block: this is a worker thread and the HTTP response has
            # already been sent.
            await asyncio.to_thread(producer.poll, _DELIVERY_POLL_SECONDS)

        try:
            task = asyncio.create_task(run())
        except RuntimeError:
            # No running loop — a synchronous caller outside the app. Nothing
            # to schedule onto, and this is not worth raising over.
            return
        # Hold a reference: a task that only the event loop knows about can be
        # garbage-collected mid-flight.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def status(self) -> KafkaStatus:
        """The value ``/health`` reports. Reads cached state; never blocks."""
        if not self.enabled:
            return "disabled"
        if self._producer is not None and self._producer.degraded:
            return "degraded"
        if self._reachable is False:
            return "degraded"
        return "ok"

    def health(self) -> dict[str, Any]:
        """Detail for ``/health``, including the last probe result."""
        detail: dict[str, Any] = {
            "status": self.status(),
            "topics": topics_for(self._settings).all if self.enabled else [],
            "reachable": self._reachable,
        }
        if self._probe_error:
            detail["last_error"] = self._probe_error
        if self._producer is not None:
            detail.update(self._producer.health())
        return detail

    async def probe_once(self) -> bool:
        """Ask the broker for cluster metadata. Never raises."""
        producer = self._producer
        if producer is None:
            return False

        def _metadata() -> Any:
            # The Producer exposes list_topics; it is the cheapest round trip
            # that proves credentials and connectivity together.
            return producer._producer.list_topics(timeout=_PROBE_TIMEOUT_SECONDS)

        try:
            await asyncio.wait_for(asyncio.to_thread(_metadata), timeout=_PROBE_TIMEOUT_SECONDS + 2)
        except Exception as exc:
            if self._reachable is not False:
                _log.warning("kafka_unreachable", error=str(exc))
            self._reachable = False
            self._probe_error = str(exc)
            return False

        if self._reachable is not True:
            _log.info("kafka_reachable", topics=topics_for(self._settings).all)
        self._reachable = True
        self._probe_error = None
        return True

    async def probe_forever(self) -> None:
        """Background connectivity loop. Started by the app's lifespan."""
        while True:
            await self.probe_once()
            await asyncio.sleep(_PROBE_INTERVAL_SECONDS)

    def start_probe(self) -> None:
        """Begin probing, if there is anything to probe."""
        if not self.enabled or self._probe_task is not None:
            return
        self._probe_task = asyncio.create_task(self.probe_forever())

    async def aclose(self) -> None:
        """Stop the probe, drain in-flight publishes, flush the producer."""
        if self._probe_task is not None:
            self._probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._probe_task
            self._probe_task = None

        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

        if self._producer is not None:
            await asyncio.to_thread(self._producer.flush, 5.0)
