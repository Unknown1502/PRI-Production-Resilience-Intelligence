# PROMPT 03 — Persistence Layer

## Task

Build persistence in `src/pri/persistence/`.

## Design

Snapshot-per-version. Do not build a fully normalized write model — we version whole states.

## Schema

SQL migration file `src/pri/persistence/migrations/001_init.sql`:

```sql
productions(id PK, title, currency, shoot_start, shoot_end, created_at)

state_versions(production_id, version, parent_version, event_id,
               snapshot JSONB NOT NULL, digest TEXT NOT NULL, created_at,
               PRIMARY KEY(production_id, version))

events(event_id PK, production_id, event_type, occurred_at, source, severity,
       payload JSONB, consumed_at, UNIQUE(event_id))    -- idempotency key

recovery_sessions(id PK, production_id, event_id, base_version, status,
                  created_at, updated_at)

candidate_plans(id PK, session_id, label, moves JSONB, valid BOOL,
                violations JSONB, score JSONB, pareto_optimal BOOL)

approvals(id PK, session_id, plan_id, approver, decision, decided_at, note)

audit_log(id PK, production_id, at, actor, action, subject, detail JSONB)

artifacts(id PK, production_id, version, kind, path, created_at)
```

Index `state_versions(production_id, version DESC)` and `events(production_id, occurred_at)`.

## Repository API

Build `src/pri/persistence/repository.py` with an async SQLAlchemy 2 repo:

```python
get_current_state(production_id) -> ProductionState
get_state(production_id, version) -> ProductionState
commit_state(state, event_id) -> int         # returns new version
record_event(event) -> bool                  # False if event_id already seen
create_session / add_candidates / record_approval / append_audit / record_artifact
```

## Critical Requirements

- `commit_state` must be **atomic** and must reject a write whose `parent_version` is not
  the current head (optimistic concurrency → raise `StaleStateError`).
- `digest = sha256` of the canonical JSON snapshot.
- Add `docker-compose.dev.yml` with `postgres:16` only.

## Tests

Write tests against a real Postgres via docker-compose. Test:
- Stale-parent rejection explicitly
- Event idempotency explicitly

## Acceptance Criteria

- Tests green
- Committing twice from the same parent raises `StaleStateError`

## Constraints

- DO NOT allow any UPDATE or DELETE on `state_versions`. Append-only.
