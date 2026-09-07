"""Server-sent events for the recovery timeline.

The demo lives or dies on whether the screens move on their own.  A judge who
has to press refresh to see the impact graph re-colour has already stopped
believing the system is reacting to anything.

This is an in-process fan-out broker: one queue per connected client, keyed by
production.  It is deliberately not durable — these are UI notifications, not
the event log.  Confluent carries the events that matter; the audit table
records what happened.  If a browser misses a frame because it reconnected
mid-recovery, it re-reads the session and catches up.

Cloud Run runs several instances behind one URL, so a client connected to
instance A will not see a recovery driven on instance B.  The consumer service
runs at min-instances 1 and the demo drives everything through one API
instance, which is enough for the hosted demo; a production deployment would
back this with Redis pub/sub or a Confluent consumer per instance.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

__all__ = ["Stage", "StreamBroker", "get_broker"]


class Stage(StrEnum):
    """The pipeline stages a client renders as a timeline.

    Ordered as they occur.  The frontend keys its timeline rows off these
    names, so they are part of the API contract.
    """

    EVENT_RECEIVED = "EVENT_RECEIVED"
    IMPACT_COMPUTED = "IMPACT_COMPUTED"
    CANDIDATES_GENERATED = "CANDIDATES_GENERATED"
    CANDIDATE_INVALID = "CANDIDATE_INVALID"
    REPLANNING = "REPLANNING"
    CANDIDATE_VALID = "CANDIDATE_VALID"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


#: Dropped frames are better than a stalled publisher. A client slow enough to
#: fill this is a client that has stopped reading.
_QUEUE_MAXSIZE = 256


class StreamBroker:
    """Fan-out of pipeline stages to every client watching a production."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)
        #: The last EVENT_RECEIVED announced per production, so the same
        #: disruption is not narrated twice.
        #:
        #: Two callers legitimately announce it: the ingest route, because the
        #: browser is already watching when the button is pressed, and the
        #: recovery service, because a disruption arriving over Kafka reaches
        #: no HTTP route at all. When both happen — the interactive path — the
        #: timeline showed "Disruption received" twice for one event.
        self._announced: dict[str, str] = {}

    def subscribe(self, production_id: str) -> asyncio.Queue[str]:
        """Register a new client and return its queue.

        Inputs:
            production_id: The production to watch.

        Outputs:
            A queue that will receive pre-formatted SSE frames.
        """
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers[production_id].add(queue)
        return queue

    def unsubscribe(self, production_id: str, queue: asyncio.Queue[str]) -> None:
        """Drop a client's queue when its connection closes."""
        subscribers = self._subscribers.get(production_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(production_id, None)

    def publish(
        self,
        production_id: str,
        stage: Stage,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Send one stage to every client watching this production.

        Non-blocking by design: publishing happens inside the recovery
        pipeline, and a wedged browser must never be able to stall a state
        transition.  A full queue drops the frame.

        Inputs:
            production_id: Which production the stage belongs to.
            stage:         The pipeline stage reached.
            payload:       Stage-specific data for the UI to render.
        """
        frame = _format(stage, payload or {})
        if stage is Stage.EVENT_RECEIVED:
            event_id = str((payload or {}).get("event_id") or "")
            if event_id and self._announced.get(production_id) == event_id:
                return
            if event_id:
                self._announced[production_id] = event_id

        for queue in list(self._subscribers.get(production_id, ())):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                continue

    def subscriber_count(self, production_id: str) -> int:
        """How many clients are currently watching — used by the demo runner."""
        return len(self._subscribers.get(production_id, ()))


def _format(stage: Stage, payload: dict[str, Any]) -> str:
    """Render one SSE frame: a named event with a JSON data line."""
    body = json.dumps(
        {"stage": stage.value, "at": datetime.now(UTC).isoformat(), **payload},
        default=str,
    )
    return f"event: {stage.value}\ndata: {body}\n\n"


_BROKER = StreamBroker()


def get_broker() -> StreamBroker:
    """Return the process-wide broker."""
    return _BROKER
