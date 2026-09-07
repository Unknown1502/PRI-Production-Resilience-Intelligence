#!/usr/bin/env python
"""Publish the LOC-04 blocked event to Confluent.

This is the button we press in the demo video. It puts a real message on a real
topic; the consumer picks it up, and everything downstream follows from that.

    python scripts/emit_disruption.py
    python scripts/emit_disruption.py --event-id evt-take-2

A fresh ``--event-id`` is needed for a second run against the same database:
the pipeline deduplicates on it, which is the correct behaviour and an
inconvenient one when rehearsing. ``--new-id`` generates one.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from pri.config import get_settings
from pri.events.producer import ProductionEventProducer
from pri.events.schemas import EventEnvelope
from pri.persistence.seed import build_disruption_event


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event-id",
        default=None,
        help="Override the event id. Defaults to the fixture's canonical id.",
    )
    parser.add_argument(
        "--new-id",
        action="store_true",
        help="Generate a fresh event id so the pipeline does not deduplicate it.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()

    event = build_disruption_event()
    if args.new_id:
        event = event.model_copy(update={"event_id": f"evt-{uuid.uuid4().hex[:10]}"})
    elif args.event_id:
        event = event.model_copy(update={"event_id": args.event_id})

    envelope = EventEnvelope.of(event)

    print(f"Publishing {event.event_type} to production.events")
    print(f"  event_id    {event.event_id}")
    print(f"  production  {event.production_id}")
    print(f"  location    {event.payload.get('location_id')}")
    print(f"  window      {event.payload.get('window_start')}")
    print(f"              {event.payload.get('window_end')}")
    print(f"  reason      {event.payload.get('reason')}")

    with ProductionEventProducer(settings) as producer:
        producer.publish_event(envelope)
        remaining = producer.flush(15.0)

    if remaining:
        print(f"\nFAILED: {remaining} message(s) never reached the broker.")
        return 1
    print("\nDelivered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
