# PROMPT 09 — FastAPI Application

## Task

Build `src/pri/api/` — a FastAPI app exposing the engine.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + git sha + version + DB connectivity |
| GET | `/api/productions/{id}/state?version=` | a state snapshot |
| GET | `/api/productions/{id}/schedule` | the shooting schedule |
| GET | `/api/productions/{id}/graph` | `graph_payload` for @xyflow/react |
| POST | `/api/productions/{id}/events` | ingest a `DisruptionEvent` |
| POST | `/api/productions/{id}/recover` | body `{event_id}` returns `RecoveryResult` |
| GET | `/api/sessions/{sid}` | session + candidates + rounds |
| POST | `/api/sessions/{sid}/approve` | `{plan_id, approver, note}` |
| POST | `/api/sessions/{sid}/execute` | `TransitionService.execute` |
| GET | `/api/productions/{id}/verification/{version}` | the six checks |
| GET | `/api/productions/{id}/audit` | append-only audit log |
| GET | `/api/artifacts/{artifact_id}` | streams the call-sheet PDF |
| POST | `/api/demo/reset` | reseed to version 1 |
| GET | `/api/stream/{production_id}` | SSE of recovery timeline events |

## The SSE stream

This is what makes the demo feel alive. Emit one event per stage:

```
EVENT_RECEIVED
IMPACT_COMPUTED
CANDIDATES_GENERATED
CANDIDATE_INVALID
REPLANNING
CANDIDATE_VALID
AWAITING_APPROVAL
APPROVED
EXECUTING
VERIFIED
```

each with a payload. The frontend renders these as they arrive.

## Cross-cutting

- `structlog` JSON logging with request ids
- CORS for the web origin
- Typed error handlers mapping domain exceptions to **409/422 with the rule code in the body**
- A settings-driven API key on all mutating routes

## Tests

httpx-based integration tests for the full happy path:
seed, event, recover, approve, execute, verify.

## Acceptance Criteria

- The integration test passes
- `/health` returns 200 with git sha and version
