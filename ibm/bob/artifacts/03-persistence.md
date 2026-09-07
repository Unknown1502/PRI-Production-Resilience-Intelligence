# Artifact 03 — Persistence & Immutable Versioning

**Prompt:** [PROMPT_03_persistence.md](../prompts/PROMPT_03_persistence.md)
**Gate:** `SCORE`
**Outcome:** complete — 24 integration tests against real PostgreSQL

---

## Plan

Snapshot-per-version, not a normalised write model. We version whole states, so
the schema stores a JSONB snapshot plus a digest and a parent pointer. The two
guarantees worth testing are optimistic concurrency and event idempotency.

## Files created

| File | Lines | Contents |
|---|---|---|
| [`persistence/migrations/001_init.sql`](../../../src/pri/persistence/migrations/001_init.sql) | — | 8 tables, 2 indexes |
| [`persistence/repository.py`](../../../src/pri/persistence/repository.py) | 985 | `PriRepository` — all database access |
| [`persistence/database.py`](../../../src/pri/persistence/database.py) | 90 | `build_engine`, `SessionFactory` |
| [`persistence/serialization.py`](../../../src/pri/persistence/serialization.py) | 118 | Canonical JSON, `state_to_snapshot`, `content_digest` |
| [`persistence/errors.py`](../../../src/pri/persistence/errors.py) | 79 | `StaleStateError` and friends |
| `docker-compose.dev.yml` | — | postgres:16, nothing else |
| `tests/persistence/` | 24 tests | Stale-parent rejection, event idempotency, round-trips |

Tables: `productions`, `events`, `state_versions`, `recovery_sessions`,
`candidate_plans`, `approvals`, `audit_log`, `artifacts`.

## Decisions

**`state_versions` is append-only, structurally.** No method on the repository
issues an UPDATE or DELETE against it. This is what forces session 08's rollback
to run inside the insert's transaction rather than deleting a bad row.

**Optimistic concurrency via `SELECT … FOR UPDATE` on the head row.** A write
whose `parent_version` is not the current head raises `StaleStateError`. Tested
explicitly: committing twice from the same parent fails the second time.

**Idempotency at the database, not in memory.** `record_event` returns `False`
for an `event_id` already stored. Session 10's consumer relies on this surviving
a process restart, which an in-memory set would not.

**Digest excludes version metadata.** `content_digest` hashes the production
content only — not `created_at`, `version`, `parent_version` or `event_id`. Two
candidate plans that arrive at the same schedule are the same plan even though
they were built a microsecond apart. `state_to_snapshot` keeps the full-snapshot
digest for the stored row, where tamper-evidence is the point.

## Later revisions

Session 08 needed reads the original prompt did not list, so the repository
gained `get_session`, `get_candidates`, `get_candidate`, `get_approval`,
`get_artifacts`, `get_artifact`, `get_audit`, `get_event`, `set_session_status`,
`head_version` and `create_session_with_id`.

`commit_state` also gained a `post_insert` hook — an async callback that runs
after the row is inserted but before the transaction commits. Raising from it
rolls the insert back. That is how verification un-writes a bad version without
a DELETE.

Session 16 added [`persistence/bootstrap.py`](../../../src/pri/persistence/bootstrap.py):
migrations plus seed in one idempotent command, for the Cloud Run job and for
`/api/demo/reset`.

## How to run it

```bash
docker compose -f docker-compose.dev.yml up -d
pytest tests/persistence -q
```

## Acceptance criteria

- ✅ Tests green against a real Postgres
- ✅ Committing twice from the same parent raises `StaleStateError`
- ✅ Duplicate `event_id` returns `False` rather than raising
- ✅ No UPDATE or DELETE path on `state_versions`

## Not covered

**No migration versioning table.** `bootstrap` re-runs every `.sql` file every
time, which works because `001_init.sql` is written with
`CREATE TABLE IF NOT EXISTS`, but a second migration that alters a column would
need Alembic or a `schema_migrations` table. Fine for a fixed-schema demo;
not fine for a second deploy.

**`get_audit` reads a fixed window.** The idempotency check in session 08 scans
the most recent 500 audit entries for a completed execution. A production with a
busier log than that would miss the replay and execute twice.
