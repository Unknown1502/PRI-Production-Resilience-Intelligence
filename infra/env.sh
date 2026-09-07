#!/usr/bin/env bash
#
# Export the deploy-time configuration from .env into the environment.
#
#   source ./infra/env.sh && ./infra/deploy.sh
#
# `deploy.sh` reads the environment, not `.env` — Cloud Run and gcloud take
# their configuration from environment variables, and the application's `.env`
# is a developer convenience the deploy has no business parsing. That leaves a
# gap where a value is correct in `.env`, unset in the shell, and the deploy
# either refuses to run or — worse, before the preflight check existed — builds
# half a stack in the wrong project.
#
# This closes it: one file remains the source of truth, and the deploy sees it.
#
# Only the variables the deploy actually needs are exported. `.env` also holds
# the local database DSN and the local API host, which must not leak into a
# Cloud Run deployment.

set -euo pipefail

_env_file="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env}"

if [ ! -f "${_env_file}" ]; then
  printf 'No .env at %s\n' "${_env_file}" >&2
  return 1 2>/dev/null || exit 1
fi

# Deliberately explicit. A blanket `export $(cat .env)` would also export
# DATABASE_URL — pointing the deployed API at localhost — and PRI_API_HOST,
# and would break on any value containing a space.
_wanted=(
  GOOGLE_CLOUD_PROJECT
  GOOGLE_CLOUD_LOCATION
  GOOGLE_GENAI_MODEL
  GOOGLE_GENAI_USE_VERTEXAI
  CONFLUENT_BOOTSTRAP_SERVERS
  CONFLUENT_API_KEY
  CONFLUENT_API_SECRET
  CONFLUENT_TOPIC_PRODUCTION_EVENTS
  CONFLUENT_TOPIC_RECOVERY_REQUESTED
  CONFLUENT_TOPIC_RECOVERY_COMPLETED
  CONFLUENT_TOPIC_AUDIT
  PRI_API_KEY
  PRI_AGENT_ENABLED
  PRI_DEMO_PRODUCTION_ID
)

for _name in "${_wanted[@]}"; do
  # Last assignment wins, matching dotenv semantics. Trailing \r is stripped:
  # a .env edited on Windows otherwise exports values ending in a carriage
  # return, which produces gcloud errors that make no sense on screen.
  _line="$(grep -E "^${_name}=" "${_env_file}" | tail -1 || true)"
  [ -z "${_line}" ] && continue
  _value="${_line#*=}"
  _value="${_value%$'\r'}"
  export "${_name}=${_value}"
done

printf 'Exported for deploy:\n'
printf '  GOOGLE_CLOUD_PROJECT   %s\n' "${GOOGLE_CLOUD_PROJECT:-(unset)}"
printf '  GOOGLE_CLOUD_LOCATION  %s\n' "${GOOGLE_CLOUD_LOCATION:-(unset)}"
printf '  GOOGLE_GENAI_MODEL     %s\n' "${GOOGLE_GENAI_MODEL:-(unset)}"
printf '  CONFLUENT_BOOTSTRAP    %s\n' "${CONFLUENT_BOOTSTRAP_SERVERS:-(unset)}"
printf '  CONFLUENT_API_KEY      %s\n' "${CONFLUENT_API_KEY:+set}"
printf '  CONFLUENT_API_SECRET   %s\n' "${CONFLUENT_API_SECRET:+set}"
printf '  PRI_API_KEY            %s\n' "${PRI_API_KEY:+set}"
printf '  PRI_AGENT_ENABLED      %s\n' "${PRI_AGENT_ENABLED:-(unset)}"

# The one thing .env cannot tell us, and the one most likely to be wrong.
_active="$(gcloud config get-value project 2>/dev/null || echo '')"
if [ -n "${_active}" ] && [ "${_active}" != "${GOOGLE_CLOUD_PROJECT:-}" ]; then
  printf '\n  WARNING: gcloud is pointed at %s, .env says %s\n' \
    "${_active}" "${GOOGLE_CLOUD_PROJECT}"
  printf '           gcloud config set project %s\n' "${GOOGLE_CLOUD_PROJECT}"
fi

unset _env_file _wanted _name _line _value _active
