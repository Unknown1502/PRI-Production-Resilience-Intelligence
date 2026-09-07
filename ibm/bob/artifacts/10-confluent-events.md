# Artifact 10 — Confluent Event Fabric

**Prompt:** [PROMPT_10_confluent_events.md](../prompts/PROMPT_10_confluent_events.md)
**Gate:** `GATE` — Stage One insurance
**Outcome:** complete — 7 tests; live cluster round-trip still to run

---

## Plan

Make Confluent the real event fabric, not a stub. Four topics, a producer with
delivery reporting, and a consumer whose two hard rules are manual offset
commits and never swallowing an exception.

## Files created

| File | Lines | Contents |
|---|---|---|
| [`events/consumer.py`](../../../src/pri/events/consumer.py) | 327 | `RecoveryConsumer`, `RecoveryInvoker`, `ApiRecoveryInvoker` |
| [`events/producer.py`](../../../src/pri/events/producer.py) | 137 | `ProductionEventProducer`, delivery callbacks |
| [`events/schemas.py`](../../../src/pri/events/schemas.py) | 111 | Envelope with `schema_version`, `to_bytes`/`from_bytes` |
| [`events/admin.py`](../../../src/pri/events/admin.py) | 97 | `AdminClient` topic provisioning |
| [`events/config.py`](../../../src/pri/events/config.py) | 92 | `Topic`, `producer_config`, `consumer_config` |
| [`events/runner.py`](../../../src/pri/events/runner.py) | 99 | `python -m pri.events.runner`, SIGTERM handling |
| [`scripts/emit_disruption.py`](../../../scripts/emit_disruption.py) | — | The button pressed in the video |
| `tests/events/test_consumer.py` | 7 tests | Idempotency, offset discipline, envelope round-trip |

Topics: `production.events`, `production.recovery.requested`,
`production.recovery.completed`, `production.audit`.

## Decisions

**Offsets commit manually, only after a message is fully handled.** A crash
between "read" and "recovered" must redeliver, not skip. `enable.auto.commit` is
`False` and the commit is the last statement in `handle`.

**Exceptions are logged, uncommitted, and re-raised.** The alternative is a
disruption that silently never produced a recovery session — precisely the
failure this whole system exists to eliminate. The process dies, Cloud Run
restarts it, the message comes back.

**Deduplication at the database.** `record_event` returns `False` for an id
already stored, so redelivery after a crash produces one recovery session, not
two. An in-memory set would not survive the restart that caused the redelivery.

**The consumer calls the API rather than importing the engine.** It could run
the pipeline in-process and the numbers would be identical — but the SSE broker
lives in the API, so a recovery computed inside the consumer would be correct
and *invisible*, and a judge watching the screen would see nothing happen.
`RecoveryInvoker` is a Protocol, so the choice is swappable and testable.

**JSON, not Avro.** The Schema Registry is configured and available, but the
payloads are small, the consumer is ours, and a judge reading a message off the
topic in the Confluent console should be able to see what it says.

**`acks=all` with idempotence on.** A disruption the broker acknowledged but did
not durably replicate is a disruption the production never hears about.

## How to run it

```bash
make topics                       # provision the four topics
make run-consumer                 # python -m pri.events.runner
python scripts/emit_disruption.py # publish the LOC-04 block
python scripts/emit_disruption.py --new-id   # rehearse without dedupe blocking you
```

## Acceptance criteria

- ✅ `confluent_kafka` genuinely imported and called — `Producer`, `Consumer`, `AdminClient`
- ✅ Same `event_id` twice produces one recovery session
- ✅ A failed handler does not commit and re-raises
- ✅ An undecodable message does not commit
- ✅ Manual offset commits throughout
- ✅ Graceful SIGTERM in the runner
- 🔲 Live round-trip through a real cluster — needs credentials

## Not covered

**No live cluster test has been run.** The seven tests use fake broker doubles
so they run in CI. `test_roundtrip.py` behind the `integration` marker is
referenced in the consumer's docstring but **not yet written** — that is the gap
between "the code is right" and "the code works against Confluent Cloud", and it
closes the moment credentials exist.

**No dead-letter topic.** A permanently poisoned message will be redelivered
forever and restart-loop the consumer. Correct for a demo, where a stuck
consumer is more visible than a silently discarded event; wrong for production.

**`production.audit` is provisioned but never published to.** The audit trail
lives in Postgres. The topic exists because the prompt specified it.
