# Artifact 09 — FastAPI Application

**Prompt:** [PROMPT_09_fastapi.md](../prompts/PROMPT_09_fastapi.md)
**Gate:** `GATE`
**Outcome:** complete — 26 end-to-end tests over HTTP against real PostgreSQL

---

## Plan

Expose the engine over HTTP, and make the demo feel alive with a server-sent
event stream that emits one frame per pipeline stage.

## Files created

| File | Lines | Contents |
|---|---|---|
| [`api/routes.py`](../../../src/pri/api/routes.py) | 542 | All 14 routes in one router |
| [`api/schemas.py`](../../../src/pri/api/schemas.py) | 444 | Request/response models + converters |
| [`api/recovery_service.py`](../../../src/pri/api/recovery_service.py) | 268 | Orchestration: load, recover, stream, persist, narrate |
| [`api/main.py`](../../../src/pri/api/main.py) | 139 | App factory, lifespan, structlog, CORS |
| [`api/errors.py`](../../../src/pri/api/errors.py) | 178 | Domain exception → HTTP mapping |
| [`api/stream.py`](../../../src/pri/api/stream.py) | 128 | `StreamBroker`, ten `Stage` values |
| [`api/deps.py`](../../../src/pri/api/deps.py) | 84 | Settings, repository, API-key guard |
| `tests/api/` | 26 tests | The whole demo path over HTTP |

## Decisions

**One router file.** The surface is fourteen routes; a judge reading the repo
should see the whole API at once rather than following imports through a
package.

**Rule codes are a response field, not a message.** `ErrorBody` carries
`rule_code` and `rule_codes` alongside `detail`. The recovery screen renders a
violation card straight off the code — making the frontend regex an error string
would be a contract that breaks the first time someone rewords a message.

**Read routes are open; mutating routes take `X-API-Key`.** An empty
`PRI_API_KEY` disables the check, which is what lets a judge drive the hosted
console with no credentials. It is a setting, so a real deployment turns it on.

**The stream replays a completed run rather than interleaving.** The recovery
loop is synchronous and takes a few milliseconds, so emitting the stages
afterwards costs the UI nothing and keeps the engine free of any knowledge that
a stream exists.

**`/health` does a real `SELECT 1`.** Cloud Run will happily route traffic to an
instance whose Cloud SQL socket has gone away, and a health check that cannot
detect that is decoration.

## Ten stream stages

```
EVENT_RECEIVED · IMPACT_COMPUTED · CANDIDATES_GENERATED · CANDIDATE_INVALID
REPLANNING · CANDIDATE_VALID · AWAITING_APPROVAL · APPROVED · EXECUTING · VERIFIED
```

Plus `FAILED`, carrying the step that refused.

## Corrections made during the build

**`/api/demo/reset` assumed a migrated schema.** It called `seed()` directly and
returned a 500 against an empty database. That is the judge's Demo button on the
hosted deployment, and "the schema is not there" is not something they can act
on. It now calls `bootstrap()` — migrations first, then seed — so it repairs an
unmigrated database instead of reporting the failure accurately. Found by
running the demo script against a database the test suite had just torn down.

**The integration tests were written with `TestClient` and did not work.**
asyncpg binds connections to the event loop that opened them, and `TestClient`
drives the app from its own portal thread, so a pool created in the test's loop
cannot be used inside a request. Rewritten with `httpx.AsyncClient` over
`ASGITransport`, which keeps everything on one loop. The failure looks like a
database problem and is not.

## How to run it

```bash
make bootstrap
make run-api                    # http://localhost:8000
pytest tests/api -q
```

## Acceptance criteria

- ✅ Integration test passes: seed → event → recover → approve → execute → verify
- ✅ `/health` returns 200 with git sha and version
- ✅ structlog JSON with a request id on every line
- ✅ CORS for the web origin
- ✅ Typed errors → 403/404/409/422 with the rule code in the body
- ✅ API key on all mutating routes

## Not covered

**The SSE broker is in-process.** A client connected to one Cloud Run instance
will not see a recovery driven on another. Adequate for a demo where the
consumer calls a single API service; a real deployment needs Redis pub/sub or a
Confluent consumer per instance. Documented in `api/stream.py`.

**No pagination anywhere.** `/audit` takes a `limit` and that is it. A
production with a year of history has no way to page through it.

**`GET /api/sessions/{sid}` returns raw database rows.** Candidates come back
with JSONB columns decoded but not re-validated into domain models, so the
frontend does more shaping than it should for that one endpoint.
