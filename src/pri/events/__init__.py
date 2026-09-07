"""Confluent Kafka event fabric.

``confluent-kafka`` is imported and called for real here — the producer in
:mod:`pri.events.producer`, the consumer in :mod:`pri.events.consumer`, and the
``AdminClient`` in :mod:`pri.events.admin`. Nothing in this package is stubbed.
"""

from pri.events.config import Topics, consumer_config, producer_config, topics_for
from pri.events.consumer import RecoveryConsumer, RecoveryInvoker, build_default_invoker
from pri.events.producer import ProductionEventProducer
from pri.events.schemas import (
    SCHEMA_VERSION,
    EventEnvelope,
    RecoveryCompleted,
    RecoveryRequested,
)

__all__ = [
    "SCHEMA_VERSION",
    "EventEnvelope",
    "ProductionEventProducer",
    "RecoveryCompleted",
    "RecoveryConsumer",
    "RecoveryInvoker",
    "RecoveryRequested",
    "Topics",
    "build_default_invoker",
    "consumer_config",
    "producer_config",
    "topics_for",
]
