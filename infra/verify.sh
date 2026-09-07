#!/usr/bin/env bash
#
# Verify a running PRI deployment end to end.
#
#   ./infra/verify.sh https://pri-api-xxxxx.run.app
#   ./infra/verify.sh                                 # defaults to localhost:8000
#
# Exits non-zero on the first genuine failure. This is the script to run on the
# judging dates, and after every deploy — a green /health only says the process
# is up, not that the pipeline works.
#
# Every check writes its evidence to docs/evidence/runtime/, because evidence
# gathered while verifying is real and evidence written afterwards is a memory.

set -uo pipefail

API="${1:-${PRI_API_BASE_URL:-http://localhost:8000}}"
API="${API%/}"
PRODUCTION="${PRI_DEMO_PRODUCTION_ID:-film-001}"
KEY="${PRI_API_KEY:-}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVIDENCE_DIR="${PRI_EVIDENCE_DIR:-${ROOT}/docs/evidence/runtime}"
mkdir -p "${EVIDENCE_DIR}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${EVIDENCE_DIR}/verify-${STAMP}.log"
exec > >(tee -a "${LOG}") 2>&1

printf 'PRI verify — %s UTC\n' "$(date -u +'%Y-%m-%d %H:%M:%S')"
printf 'target: %s\n\n' "${API}"

PASS=0
FAIL=0

ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$*"; PASS=$((PASS + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL + 1)); }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# curl with the API key when one is configured. --fail is deliberately NOT set:
# the body of a non-2xx is often the useful part.
call() {
  local method="$1" path="$2"
  shift 2
  if [ -n "${KEY}" ]; then
    curl -sS -X "${method}" -H "X-API-Key: ${KEY}" "$@" "${API}${path}"
  else
    curl -sS -X "${method}" "$@" "${API}${path}"
  fi
}

status_of() {
  local method="$1" path="$2"
  shift 2
  if [ -n "${KEY}" ]; then
    curl -sS -o /dev/null -w '%{http_code}' -X "${method}" \
      -H "X-API-Key: ${KEY}" "$@" "${API}${path}"
  else
    curl -sS -o /dev/null -w '%{http_code}' -X "${method}" "$@" "${API}${path}"
  fi
}

# ---------------------------------------------------------------------------
step "1 · Health"
# ---------------------------------------------------------------------------

HEALTH="$(call GET /health || true)"
HEALTH_CODE="$(status_of GET /health || true)"
printf '  %s\n' "${HEALTH}"

if [ "${HEALTH_CODE}" = "200" ]; then
  ok "/health returns 200"
else
  bad "/health returned ${HEALTH_CODE}, expected 200"
fi

case "${HEALTH}" in
  *'"database":"ok"'*|*'"database": "ok"'*) ok "database ok" ;;
  *) bad "database is not ok" ;;
esac

# Kafka degraded is explicitly acceptable — PRI works without it.
case "${HEALTH}" in
  *'"kafka":"ok"'*|*'"kafka": "ok"'*)             ok "kafka ok" ;;
  *'"kafka":"degraded"'*|*'"kafka": "degraded"'*) ok "kafka degraded (allowed: not on the critical path)" ;;
  *'"kafka":"disabled"'*|*'"kafka": "disabled"'*) ok "kafka disabled (no credentials configured)" ;;
  *) bad "no kafka status in /health" ;;
esac

# ---------------------------------------------------------------------------
step "2 · The overview returns a production"
# ---------------------------------------------------------------------------

SCHEDULE="$(call GET "/api/productions/${PRODUCTION}/schedule" || true)"
case "${SCHEDULE}" in
  *'"days"'*) ok "schedule for ${PRODUCTION} has days" ;;
  *) bad "no schedule for ${PRODUCTION}: ${SCHEDULE:0:200}" ;;
esac

# ---------------------------------------------------------------------------
step "3 · A disruption produces candidates, including an invalid one"
# ---------------------------------------------------------------------------

EVENT_ID="verify-${STAMP}"
# severity is a float, not a word. The payload mirrors the canonical demo
# disruption so the recovery has something real to chew on.
EVENT_BODY=$(cat <<JSON
{"event_id":"${EVENT_ID}","event_type":"location.blocked",
 "occurred_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","source":"verify.sh","severity":0.8,
 "payload":{"location_id":"${PRI_VERIFY_LOCATION:-LOC-04}",
            "window_start":"${PRI_VERIFY_WINDOW_START:-2026-09-10T00:00:00+05:30}",
            "window_end":"${PRI_VERIFY_WINDOW_END:-2026-09-11T00:00:00+05:30}",
            "reason":"verify.sh smoke test"}}
JSON
)

INGEST="$(call POST "/api/productions/${PRODUCTION}/events" \
  -H 'Content-Type: application/json' -d "${EVENT_BODY}" || true)"
case "${INGEST}" in
  *'"event_id"'*) ok "event accepted" ;;
  *) bad "event rejected: ${INGEST:0:200}" ;;
esac

RECOVER="$(call POST "/api/productions/${PRODUCTION}/recover" \
  -H 'Content-Type: application/json' \
  -d "{\"event_id\":\"${EVENT_ID}\"}" || true)"

case "${RECOVER}" in
  *'"candidates"'*|*'"evaluated"'*) ok "recovery returned candidates" ;;
  *) bad "recovery returned no candidates: ${RECOVER:0:300}" ;;
esac

# The rejected plan is the whole point: it is the evidence that validation is
# real rather than decorative.
case "${RECOVER}" in
  *'"valid":false'*|*'"valid": false'*) ok "at least one candidate was rejected by the validator" ;;
  *) bad "every candidate was valid — the invalid-plan evidence is missing" ;;
esac

case "${RECOVER}" in
  *C001*) ok "a constraint code appears in the response" ;;
  *) printf '  ....  no C001 in this run (not necessarily wrong)\n' ;;
esac

# ---------------------------------------------------------------------------
step "4 · Kafka actually delivered something"
# ---------------------------------------------------------------------------
#
# The event posted above is published to Confluent fire-and-forget. Absence of
# an error proves nothing: a publish to a topic that does not exist returns
# normally and is rejected later in the delivery callback, which PRI logs at
# WARNING. Only an acknowledgement carrying a partition and an offset proves
# the message landed, so that is what is checked.

sleep 3
KAFKA_HEALTH="$(call GET /health || true)"

case "${KAFKA_HEALTH}" in
  *'"kafka":"disabled"'*|*'"kafka": "disabled"'*)
    printf '  ....  kafka disabled on this deployment; nothing to prove
'
    ;;
  *'"last_delivery":{'*|*'"last_delivery": {'*)
    OFFSET="$(printf '%s' "${KAFKA_HEALTH}" | grep -o '"offset":[0-9]*' | head -1)"
    PARTITION="$(printf '%s' "${KAFKA_HEALTH}" | grep -o '"partition":[0-9]*' | head -1)"
    ok "broker acknowledged a message (${PARTITION}, ${OFFSET})"
    ;;
  *'"last_delivery":null'*|*'"last_delivery": null'*)
    bad "nothing has been acknowledged by the broker — the topics may not exist"
    printf '      Run: python -m pri.events.smoke --create
'
    printf '      %s
' "$(printf '%s' "${KAFKA_HEALTH}" | grep -o '"last_error":"[^"]*"' | head -1)"
    ;;
  *)
    bad "no last_delivery field in /health — is this an older build?"
    ;;
esac

# ---------------------------------------------------------------------------
step "5 · Demo reset"
# ---------------------------------------------------------------------------

RESET_CODE="$(status_of POST /api/demo/reset -H 'Content-Type: application/json' -d '{}' || true)"
if [ "${RESET_CODE}" = "200" ]; then
  ok "demo reset succeeded"
else
  bad "demo reset returned ${RESET_CODE}"
fi

AFTER="$(call GET "/api/productions/${PRODUCTION}/schedule" || true)"
case "${AFTER}" in
  *'"version":1'*|*'"version": 1'*) ok "reset left the production at version 1" ;;
  *) bad "after reset the production is not at v1" ;;
esac

# ---------------------------------------------------------------------------
step "6 · A call sheet downloads as a real PDF"
# ---------------------------------------------------------------------------

PDF="${EVIDENCE_DIR}/call-sheet-${STAMP}.pdf"
DAY="$(printf '%s' "${AFTER}" | grep -o '"date":"[0-9-]*"' | head -1 | cut -d'"' -f4)"

if [ -z "${DAY}" ]; then
  bad "could not find a shooting day to render"
else
  if [ -n "${KEY}" ]; then
    curl -sS -H "X-API-Key: ${KEY}" -o "${PDF}" \
      "${API}/api/productions/${PRODUCTION}/call-sheet/${DAY}" || true
  else
    curl -sS -o "${PDF}" \
      "${API}/api/productions/${PRODUCTION}/call-sheet/${DAY}" || true
  fi

  if [ -s "${PDF}" ] && [ "$(head -c 4 "${PDF}")" = "%PDF" ]; then
    ok "call sheet for ${DAY} is a PDF ($(wc -c < "${PDF}" | tr -d ' ') bytes)"
  else
    bad "call sheet for ${DAY} is not a PDF"
    head -c 200 "${PDF}" 2>/dev/null || true
  fi
fi

# ---------------------------------------------------------------------------

printf '\n\033[1m%d passed, %d failed\033[0m\n' "${PASS}" "${FAIL}"
printf 'evidence: %s\n' "${LOG}"

[ "${FAIL}" -eq 0 ] || exit 1
