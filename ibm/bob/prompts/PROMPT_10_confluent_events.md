# PROMPT 10 — Confluent Event Fabric

> `[GATE — Stage One insurance]`

## Task

Build `src/pri/events/` on `confluent-kafka`. This is our partner-service runtime
integration and must be **genuinely imported and called, not stubbed**.

## Topics

Create via `src/pri/events/admin.py` using `AdminClient`:

```
production.events
production.recovery.requested
production.recovery.completed
production.audit
```

## Modules

| File | Responsibility |
|---|---|
| `schemas.py` | Pydantic envelope matching `DisruptionEvent`, with `to_bytes`/`from_bytes` JSON serialisation and a `schema_version` field |
| `producer.py` | `ProductionEventProducer` with `publish(topic, key, event)`, delivery callbacks logged, `flush()` on shutdown |
| `consumer.py` | `RecoveryConsumer`: consumes `production.events`, dedupes on `event_id` via `repository.record_event` (returns `False` so the message is skipped), invokes the recovery pipeline, publishes to `production.recovery.requested` and `production.recovery.completed`, commits offsets **MANUALLY only after successful handling** |
| `runner.py` | `python -m pri.events.runner` entrypoint, graceful SIGTERM |

## Config

Bootstrap servers, API key/secret, `SASL_SSL` + `PLAIN`, consumer group
`pri-recovery` — **from `.env` only**.

## Demo trigger

Add `scripts/emit_disruption.py` — a CLI that publishes the **LOC-04 blocked**
event. This is the button we press in the demo video.

## Tests

- An idempotency test (same `event_id` twice produces **one** recovery session) using a
  fake producer/consumer double
- One live smoke test guarded by `@pytest.mark.integration` that round-trips a
  message through the real cluster

## Acceptance Criteria

`python scripts/emit_disruption.py` publishes, the runner consumes, and a
recovery session appears in Postgres.

## Constraints

- DO NOT swallow consumer exceptions. **Log, do not commit the offset, and re-raise.**
