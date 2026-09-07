"""Wire format for the Confluent topics.

An envelope rather than a bare event, because the thing on the topic outlives
the code that wrote it.  ``schema_version`` is what lets a consumer built next
month read a message produced today and know whether it can.

JSON rather than Avro: the Schema Registry is configured and available, but the
payloads here are small, the consumer is ours, and a judge reading a message off
the topic in the Confluent console should be able to see what it says.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field

from pri.domain.models import DisruptionEvent

__all__ = ["SCHEMA_VERSION", "EventEnvelope", "RecoveryCompleted", "RecoveryRequested"]

#: Bumped whenever the envelope's own shape changes, not when a payload does.
SCHEMA_VERSION = 1


class _Envelope(BaseModel, frozen=True):
    """Fields shared by every message PRI publishes."""

    schema_version: int = Field(default=SCHEMA_VERSION)
    produced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    producer: str = "pri"

    def to_bytes(self) -> bytes:
        """Serialise to the UTF-8 JSON that goes on the topic."""
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> Self:
        """Parse a message body.

        Failure modes:
            Raises ``pydantic.ValidationError`` if the body does not match this
            envelope, and ``json.JSONDecodeError`` if it is not JSON. Both are
            fatal for the message: a consumer must not commit an offset for
            something it could not read.
        """
        return cls.model_validate_json(raw.decode("utf-8"))


class EventEnvelope(_Envelope, frozen=True):
    """A disruption on ``production.events``.

    Inputs:
        production_id: The production affected.
        event:         The disruption itself, unchanged from the domain model.
    """

    kind: Literal["disruption"] = "disruption"
    production_id: str
    event: DisruptionEvent

    @classmethod
    def of(cls, event: DisruptionEvent) -> EventEnvelope:
        """Wrap a domain event for publication."""
        return cls(production_id=event.production_id, event=event)

    @property
    def key(self) -> str:
        """Partition key: one production's events stay ordered relative to each other."""
        return self.production_id


class RecoveryRequested(_Envelope, frozen=True):
    """Published when the consumer starts working on an event."""

    kind: Literal["recovery.requested"] = "recovery.requested"
    production_id: str
    event_id: str
    base_version: int


class RecoveryCompleted(_Envelope, frozen=True):
    """Published when recovery options are ready for a human.

    Carries enough for a downstream system to route an approval request without
    re-reading the database: which plan is recommended, and what it costs.
    """

    kind: Literal["recovery.completed"] = "recovery.completed"
    production_id: str
    event_id: str
    session_id: str
    candidate_count: int
    valid_count: int
    pareto_plan_ids: list[str]
    recommended_plan_id: str | None
    failed_rule_codes: list[str] = Field(default_factory=list)


def parse_envelope(raw: bytes) -> dict[str, Any]:
    """Decode any PRI message far enough to see what it is.

    Used by tooling that tails a topic and does not know which kind is coming.

    Failure modes:
        Raises ``json.JSONDecodeError`` on a non-JSON body.
    """
    parsed: dict[str, Any] = json.loads(raw.decode("utf-8"))
    return parsed
