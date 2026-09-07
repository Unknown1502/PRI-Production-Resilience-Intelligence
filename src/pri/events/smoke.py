"""``python -m pri.events.smoke`` — prove the Confluent integration is real.

Run this the first time PRI meets a live cluster, and again after any change to
topic names or credentials.

The failure this exists to catch: a publish to a topic that does not exist
costs nothing observable. On a cluster with auto-create enabled the message
lands in a topic nobody is watching; on one without it — which is the default
for Confluent Cloud Basic — the produce call still returns, and the rejection
arrives later in the delivery callback, where PRI deliberately logs it at
WARNING rather than raising. Either way the console shows an empty topic and
the log shows no error.

So this script does not report success on the absence of an error. It waits for
the broker's acknowledgement and prints the partition and offset it came back
with, and exits non-zero if none arrives.

    python -m pri.events.smoke              # publish to every topic
    python -m pri.events.smoke --list       # show what the cluster has
    python -m pri.events.smoke --create     # create the missing topics first
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pri.config import get_settings
from pri.events.config import topics_for
from pri.events.producer import ProductionEventProducer

if TYPE_CHECKING:
    from pri.config import Settings

__all__ = ["main", "smoke_test"]

#: How long to wait for the broker to acknowledge. Generous — this is a
#: diagnostic, not a request path.
_FLUSH_SECONDS = 20.0


def _describe(settings: Settings) -> None:
    topics = topics_for(settings)
    print(f"bootstrap : {settings.confluent_bootstrap_servers or '(not set)'}")
    print(f"credentials: {'present' if settings.confluent_configured else 'MISSING'}")
    print("topics    :")
    for name in topics.all:
        print(f"    {name}")
    print()


def smoke_test(settings: Settings | None = None) -> int:
    """Publish one message to every topic and wait for acknowledgements.

    Outputs:
        0 when every topic acknowledged, 1 otherwise.
    """
    resolved = settings if settings is not None else get_settings()
    _describe(resolved)

    if not resolved.confluent_configured:
        print("FAIL: no Confluent credentials. Set CONFLUENT_BOOTSTRAP_SERVERS,")
        print("      CONFLUENT_API_KEY and CONFLUENT_API_SECRET.")
        return 1

    topics = topics_for(resolved)
    stamp = datetime.now(UTC).isoformat()
    failures: list[str] = []

    with ProductionEventProducer(resolved) as producer:
        for name in topics.all:
            payload = f'{{"kind":"smoke","at":"{stamp}","topic":"{name}"}}'.encode()
            result = producer.publish(name, "smoke", payload)
            if not result.ok:
                print(f"  {name}: REFUSED at produce — {result.error}")
                failures.append(name)
                continue

            before = producer.delivered
            producer.flush(_FLUSH_SECONDS)

            ack = producer.last_delivery
            if producer.delivered > before and ack is not None:
                print(f"  {name}: DELIVERED  partition={ack['partition']} offset={ack['offset']}")
            else:
                # The produce call succeeded and the broker still did not take
                # it. This is what an absent topic looks like.
                print(f"  {name}: NO ACKNOWLEDGEMENT — {producer.health()['last_error']}")
                failures.append(name)

    print()
    if failures:
        print(f"FAIL: {len(failures)} of {len(topics.all)} topics did not acknowledge.")
        print("      Create them with: python -m pri.events.smoke --create")
        print("      The names above are exactly what the cluster must have.")
        return 1

    print(f"PASS: all {len(topics.all)} topics acknowledged with a partition and offset.")
    return 0


def main() -> None:
    """Entrypoint."""
    parser = argparse.ArgumentParser(description="Prove the Confluent integration works.")
    parser.add_argument("--list", action="store_true", help="list the cluster's topics")
    parser.add_argument("--create", action="store_true", help="create missing topics first")
    args = parser.parse_args()

    settings = get_settings()

    if args.create:
        from pri.events.admin import create_topics

        for name, outcome in create_topics(settings).items():
            print(f"  {name}: {outcome}")
        print()

    if args.list:
        from pri.events.admin import list_topics

        _describe(settings)
        for name in sorted(list_topics(settings)):
            print(f"  {name}")
        raise SystemExit(0)

    raise SystemExit(smoke_test(settings))


if __name__ == "__main__":
    main()
