"""Topic provisioning.

Creating topics from code rather than clicking them into the Confluent console
means the cluster can be rebuilt from the repository, and it means a reviewer
can see the partition and replication choices instead of taking them on trust.

Idempotent: a topic that already exists is reported and skipped, so this is safe
to run on every deploy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from confluent_kafka.admin import AdminClient, NewTopic  # type: ignore[attr-defined]

from pri.events.config import _base_config, topics_for

if TYPE_CHECKING:
    from pri.config import Settings

__all__ = ["create_topics", "list_topics", "main"]

_log = structlog.get_logger(__name__)

#: Confluent Cloud's Basic tier replicates three ways and rejects anything else.
_REPLICATION_FACTOR = 3
_PARTITIONS = 3

#: A local single-broker cluster cannot satisfy a replication factor of 3.
_LOCAL_REPLICATION_FACTOR = 1


def _replication_for(settings: Settings) -> int:
    """Three on Confluent Cloud, one against a local broker."""
    key = settings.confluent_api_key.get_secret_value()
    is_cloud = bool(key) and not key.startswith("REPLACE_WITH")
    return _REPLICATION_FACTOR if is_cloud else _LOCAL_REPLICATION_FACTOR


def create_topics(settings: Settings, admin: Any | None = None) -> dict[str, str]:
    """Create every PRI topic that does not already exist.

    Inputs:
        settings: Cluster connection details.
        admin:    Inject a double in tests.

    Outputs:
        A mapping of topic name to ``"created"``, ``"exists"`` or an error
        string, so a deploy script can print a table rather than a stack trace.

    Failure modes:
        Does not raise for a topic that already exists. Other broker errors are
        captured per-topic in the returned mapping.
    """
    client = admin if admin is not None else AdminClient(_base_config(settings))
    replication = _replication_for(settings)

    requested = [
        NewTopic(name, num_partitions=_PARTITIONS, replication_factor=replication)
        for name in topics_for(settings).all
    ]
    results: dict[str, str] = {}
    for name, future in client.create_topics(requested).items():
        try:
            future.result()
            results[name] = "created"
            _log.info("kafka_topic_created", topic=name, replication=replication)
        except Exception as exc:
            message = str(exc)
            if "already exists" in message.lower():
                results[name] = "exists"
            else:
                results[name] = message
                _log.error("kafka_topic_failed", topic=name, error=message)
    return results


def list_topics(settings: Settings, admin: Any | None = None) -> list[str]:
    """Return every topic the cluster knows about."""
    client = admin if admin is not None else AdminClient(_base_config(settings))
    metadata = client.list_topics(timeout=10)
    return sorted(metadata.topics)


def main() -> None:
    """``python -m pri.events.admin`` — provision the topics and print the result."""
    from pri.config import get_settings

    outcomes = create_topics(get_settings())
    for topic, outcome in sorted(outcomes.items()):
        print(f"{topic:40s} {outcome}")


if __name__ == "__main__":
    main()
