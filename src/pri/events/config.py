"""Confluent client configuration, assembled from settings only.

No credential is ever written here or defaulted. ``SASL_SSL`` with ``PLAIN`` is
what Confluent Cloud expects; a local broker with no auth is detected by the
absence of an API key so ``docker compose`` development does not need fake
credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pri.config import Settings

__all__ = ["Topics", "consumer_config", "producer_config", "topics_for"]


@dataclass(frozen=True, slots=True)
class Topics:
    """The four topics PRI uses, resolved from settings.

    These were class constants — ``production.events`` and friends — while
    ``.env`` and ``Settings`` configured a differently named, ``pri.``-prefixed
    set that nothing read. The producer therefore wrote to topics the operator
    had never heard of, and on a cluster with auto-create enabled that looks
    exactly like a working integration: publishes succeed, and the topics the
    operator is watching stay empty.

    One definition, read from settings, so the name in the log line and the
    name on the cluster are the same string.
    """

    events: str
    recovery_requested: str
    recovery_completed: str
    audit: str

    @property
    def all(self) -> tuple[str, ...]:
        """Every topic, for the admin client that creates them."""
        return (self.events, self.recovery_requested, self.recovery_completed, self.audit)


def topics_for(settings: Settings) -> Topics:
    """Resolve the topic names this deployment uses."""
    return Topics(
        events=settings.confluent_topic_production_events,
        recovery_requested=settings.confluent_topic_recovery_requested,
        recovery_completed=settings.confluent_topic_recovery_completed,
        audit=settings.confluent_topic_audit,
    )


def _base_config(settings: Settings) -> dict[str, Any]:
    """Bootstrap and auth, shared by producer, consumer and admin clients."""
    config: dict[str, Any] = {
        "bootstrap.servers": settings.confluent_bootstrap_servers,
        "client.id": f"pri-{settings.pri_env}",
    }
    api_key = settings.confluent_api_key.get_secret_value()
    api_secret = settings.confluent_api_secret.get_secret_value()
    if api_key and not api_key.startswith("REPLACE_WITH"):
        config.update(
            {
                "security.protocol": "SASL_SSL",
                "sasl.mechanisms": "PLAIN",
                "sasl.username": api_key,
                "sasl.password": api_secret,
            }
        )
    return config


def producer_config(settings: Settings) -> dict[str, Any]:
    """Producer configuration.

    ``acks=all`` with idempotence on: a disruption that the broker acknowledged
    but did not durably replicate is a disruption the production never hears
    about.
    """
    return {
        **_base_config(settings),
        "acks": "all",
        "enable.idempotence": True,
        "linger.ms": 5,
        "compression.type": "lz4",
        # librdkafka's defaults here are measured in minutes. A publish on a
        # path a browser is waiting on must give up in seconds and let the
        # caller degrade, so every timeout is bounded and configurable.
        "socket.timeout.ms": settings.kafka_socket_timeout_ms,
        "message.timeout.ms": settings.kafka_message_timeout_ms,
        "request.timeout.ms": settings.kafka_request_timeout_ms,
        "delivery.timeout.ms": settings.kafka_delivery_timeout_ms,
    }


def consumer_config(settings: Settings, group_id: str = "pri-recovery") -> dict[str, Any]:
    """Consumer configuration.

    Auto-commit is off. Offsets advance only after a message has been fully
    handled and its recovery session written — otherwise a crash mid-recovery
    would lose the event entirely, which is exactly the failure the whole
    system exists to prevent.
    """
    return {
        **_base_config(settings),
        "group.id": group_id,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "session.timeout.ms": 45000,
        "max.poll.interval.ms": 300000,
    }
