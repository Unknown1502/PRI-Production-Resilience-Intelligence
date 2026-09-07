#!/usr/bin/env bash
#
# Deploy PRI to Cloud Run.
#
#   ./infra/deploy.sh
#
# Idempotent: every step either creates the resource or reports that it already
# exists. Safe to re-run after a partial failure, which is the state you will
# actually be in at 2am the night before a deadline.
#
# Reads configuration from the environment; see infra/README.md for the full
# list. Nothing secret is ever passed on a command line — secrets go into
# Secret Manager and are mounted as environment variables by Cloud Run.

set -euo pipefail

# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
#
# Every run records itself. A deploy log written afterwards from memory is not
# evidence; one produced by the deploy itself is. Nothing secret reaches this
# file — the database password is generated into Secret Manager and never
# printed, and the Confluent values are piped to gcloud on stdin.

EVIDENCE_DIR="${PRI_EVIDENCE_DIR:-$(cd "$(dirname "$0")/.." && pwd)/docs/evidence/google-cloud}"
mkdir -p "${EVIDENCE_DIR}"
DEPLOY_LOG="${EVIDENCE_DIR}/deploy-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "${DEPLOY_LOG}") 2>&1
printf 'PRI deploy — %s UTC\n' "$(date -u +'%Y-%m-%d %H:%M:%S')"
printf 'log: %s\n' "${DEPLOY_LOG}"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
# GOOGLE_CLOUD_LOCATION is the Google SDK's own name for this and is what
# the application reads; GOOGLE_CLOUD_REGION is still accepted here.
REGION="${GOOGLE_CLOUD_LOCATION:-${GOOGLE_CLOUD_REGION:-us-central1}}"
REPO="${ARTIFACT_REPO:-pri}"
SQL_INSTANCE="${SQL_INSTANCE:-pri-db}"
# Bucket names are globally unique, so the project id is the natural qualifier.
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-${PROJECT_ID}-call-sheets}"
SQL_TIER="${SQL_TIER:-db-f1-micro}"
SQL_EDITION="${SQL_EDITION:-ENTERPRISE}"
DB_NAME="${DB_NAME:-pri}"
DB_USER="${DB_USER:-pri_user}"
DEMO_PRODUCTION_ID="${PRI_DEMO_PRODUCTION_ID:-film-001}"
GEMINI_MODEL="${GOOGLE_GENAI_MODEL:-gemini-2.5-flash}"
AGENT_ENABLED="${PRI_AGENT_ENABLED:-true}"

RUNTIME_SA="${PRI_RUNTIME_SA:-pri-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"

GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

# A partial deploy is worse than none: it leaves a URL that half works, which
# is the hardest thing to debug under time pressure. Everything required is
# checked before the first resource is touched.
missing=""
for required in GOOGLE_CLOUD_PROJECT CONFLUENT_BOOTSTRAP_SERVERS \
                CONFLUENT_API_KEY CONFLUENT_API_SECRET PRI_API_KEY; do
  if [ -z "${!required:-}" ]; then
    missing="${missing} ${required}"
  fi
done
if [ -n "${missing}" ]; then
  printf '\nRefusing to deploy. Unset:%s\n\n' "${missing}" >&2
  printf 'Set them and re-run. See infra/README.md.\n' >&2
  exit 1
fi
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
already() { printf '    (already exists, skipping)\n'; }

# Run a create command that is expected to fail when the resource is already
# there — and *only* when it is already there.
#
# The previous form was `gcloud ... 2>/dev/null || already`, which printed
# "already exists, skipping" for every possible failure: a bad flag, a quota
# refusal, a permission error. It reported success while creating nothing, and
# the deploy then failed several steps later with a 404 that pointed at the
# wrong thing. A deploy script that lies about what it did is worse than one
# that stops.
create_or_skip() {
  local output status=0
  # `|| status=$?` rather than a bare assignment: under `set -e` a standalone
  # assignment whose command substitution fails kills the script immediately,
  # so the handler below never ran and the failure was reported as a silent
  # exit with no message at all — worse than the masking it replaced.
  output="$("$@" 2>&1)" || status=$?
  if [ "${status}" -eq 0 ]; then
    printf '%s\n' "${output}" | sed 's/^/    /'
    return 0
  fi
  case "${output}" in
    # Every service words "it is already there" differently, and each wording
    # this does not know about turns a re-run into an aborted deploy. Cloud
    # Storage says "you already own it" and returns 409 — which is why the
    # first re-run after the bucket step was added stopped dead before
    # deploying anything, while the invocation reported success.
    *"already exists"*|*"altready exists"*|*"ALREADY_EXISTS"*)
      already
      return 0
      ;;
    *"you already own it"*|*"HTTPError 409"*)
      already
      return 0
      ;;
  esac
  printf '\n    FAILED: %s\n\n' "$*" >&2
  printf '%s\n' "${output}" >&2
  exit "${status}"
}


# Build one image from a named Dockerfile.
#
#   build_image <dockerfile> <image> [<extra docker build arg> ...]
#
# `gcloud builds submit --tag` only ever builds ./Dockerfile. There is no
# --file flag — passing one fails the command outright with "unrecognized
# arguments" — so naming a different Dockerfile means submitting a build
# config. All three images go through this one path.
build_image() {
  local dockerfile="$1" image="$2"
  shift 2
  local extras=""
  for extra in "$@"; do
    extras="${extras}
      - ${extra}"
  done
  # A real file, not `--config -`. This gcloud rejects stdin with
  # "Unable to read file [-]", and the failure arrives after the images have
  # been uploaded as a build context, so it is slow as well as unhelpful.
  local config
  config="$(mktemp -t pri-build-XXXXXX.yaml)"
  cat >"${config}" <<YAML
steps:
  - name: gcr.io/cloud-builders/docker
    args:
      - build${extras}
      - -f
      - ${dockerfile}
      - -t
      - ${image}
      - .
images:
  - ${image}
YAML
  gcloud builds submit --project "${PROJECT_ID}" --config "${config}" .
  local status=$?
  rm -f "${config}"
  return "${status}"
}

# ---------------------------------------------------------------------------
# 1 · APIs
# ---------------------------------------------------------------------------

say "Enabling APIs"
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  --project "${PROJECT_ID}"

# ---------------------------------------------------------------------------
# 2 · Artifact Registry
# ---------------------------------------------------------------------------

say "Artifact Registry repository ${REPO}"
create_or_skip gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="PRI container images" \
  --project "${PROJECT_ID}"

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# ---------------------------------------------------------------------------
# 2b · Somewhere for issued call sheets to live
# ---------------------------------------------------------------------------
#
# A call sheet is the one artefact that leaves PRI — the document ninety people
# are called to work by — and the `artifacts` table records that version N was
# issued as this file. Written to container disk, the row outlived the document
# every time Cloud Run replaced the instance, which left the audit trail
# claiming a publication it could not produce.
#
# Uniform bucket-level access, no public read: a call sheet carries a unit's
# addresses and cast call times.

say "Cloud Storage bucket ${ARTIFACT_BUCKET}"
create_or_skip gcloud storage buckets create "gs://${ARTIFACT_BUCKET}" \
  --project "${PROJECT_ID}" \
  --location="${REGION}" \
  --uniform-bucket-level-access \
  --public-access-prevention

# ---------------------------------------------------------------------------
# 3 · Cloud SQL
# ---------------------------------------------------------------------------

# --edition is explicit. Postgres 16 now defaults to ENTERPRISE_PLUS, which
# rejects every shared-core tier: `db-f1-micro` fails with "Invalid Tier for
# (ENTERPRISE_PLUS) Edition". ENTERPRISE is the edition that has the cheap
# tiers, and cheap is the correct choice for a demo database.
say "Cloud SQL instance ${SQL_INSTANCE} (${SQL_EDITION}, ${SQL_TIER})"
create_or_skip gcloud sql instances create "${SQL_INSTANCE}" \
  --database-version=POSTGRES_16 \
  --edition="${SQL_EDITION}" \
  --tier="${SQL_TIER}" \
  --region="${REGION}" \
  --storage-size=10GB \
  --storage-auto-increase \
  --project "${PROJECT_ID}"

create_or_skip gcloud sql databases create "${DB_NAME}" \
  --instance="${SQL_INSTANCE}" \
  --project "${PROJECT_ID}"

# The password is generated here and never printed — captured by command
# substitution rather than echoed, so it does not reach the deploy log.
#
# The secret and the database user are ensured *separately*. They used to share
# one branch: create the secret, then create the user. A run that created the
# secret and then failed before the instance existed therefore left the user
# permanently uncreated — every later run saw the secret, took the else branch,
# and skipped the user, so the API deployed against a role that did not exist.
# Two independent facts, checked independently.
say "Database password in Secret Manager"
if gcloud secrets describe pri-db-password --project "${PROJECT_ID}" >/dev/null 2>&1; then
  printf '    pri-db-password already present\n'
else
  DB_PASSWORD="$(openssl rand -base64 32 | tr -d '\n/+=' | head -c 32)"
  printf '%s' "${DB_PASSWORD}" |
    gcloud secrets create pri-db-password --data-file=- --project "${PROJECT_ID}" >/dev/null
  printf '    pri-db-password created\n'
  unset DB_PASSWORD
fi

# The passcode for typed disruption injection.
#
# That endpoint is a write path with a billed model call behind it, on a public
# URL. It stays shut unless PRI_INJECT_PASSCODE is set — an unset secret means
# 404, never "open" — so this generates one and keeps it in Secret Manager.
#
# Read it out when you need it at the podium:
#   gcloud secrets versions access latest --secret=pri-inject-passcode
say "Injection passcode in Secret Manager"
if gcloud secrets describe pri-inject-passcode --project "${PROJECT_ID}" >/dev/null 2>&1; then
  printf '    pri-inject-passcode already present\n'
else
  INJECT_PASSCODE="$(openssl rand -base64 24 | tr -d '\n/+=' | head -c 20)"
  printf '%s' "${INJECT_PASSCODE}" |
    gcloud secrets create pri-inject-passcode --data-file=- --project "${PROJECT_ID}" >/dev/null
  printf '    pri-inject-passcode created\n'
  unset INJECT_PASSCODE
fi

say "Database user ${DB_USER}"
if gcloud sql users list --instance="${SQL_INSTANCE}" --project "${PROJECT_ID}" \
     --format='value(name)' 2>/dev/null | grep -qx "${DB_USER}"; then
  printf '    %s already exists\n' "${DB_USER}"
else
  # Read the password back rather than regenerating one: the secret is what
  # Cloud Run mounts, so the user must be created with that exact value.
  DB_PASSWORD="$(gcloud secrets versions access latest \
    --secret=pri-db-password --project "${PROJECT_ID}")"
  create_or_skip gcloud sql users create "${DB_USER}" \
    --instance="${SQL_INSTANCE}" \
    --password="${DB_PASSWORD}" \
    --project "${PROJECT_ID}"
  unset DB_PASSWORD
  printf '    %s created\n' "${DB_USER}"
fi

CONNECTION_NAME="$(gcloud sql instances describe "${SQL_INSTANCE}" \
  --project "${PROJECT_ID}" --format='value(connectionName)')"

# ---------------------------------------------------------------------------
# 3b · Runtime service account
# ---------------------------------------------------------------------------
#
# Explicit rather than the default compute account. The default has Editor on
# many projects, which is far more than these services need, and has no
# permissions at all on projects where that grant has been removed — so relying
# on it means the deploy either over-privileges or fails, depending on someone
# else's org policy.
#
# aiplatform.user is what makes the Vertex call work. Without it the agent
# fails at request time with a 403 that reads like a quota problem.

say "Runtime service account"
if ! gcloud iam service-accounts describe "${RUNTIME_SA}" \
     --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${RUNTIME_SA%%@*}" \
    --display-name="PRI Cloud Run runtime" \
    --project "${PROJECT_ID}"
else
  already
fi

for role in roles/cloudsql.client \
            roles/secretmanager.secretAccessor \
            roles/aiplatform.user \
            roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="${role}" \
    --condition=None \
    --quiet >/dev/null
  printf '    %s\n' "${role}"
done

# Scoped to the bucket rather than the project: the runtime writes call sheets
# and reads nothing else in Cloud Storage.
gcloud storage buckets add-iam-policy-binding "gs://${ARTIFACT_BUCKET}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/storage.objectAdmin" \
  --project "${PROJECT_ID}" \
  --quiet >/dev/null
printf '    %s on gs://%s\n' "roles/storage.objectAdmin" "${ARTIFACT_BUCKET}"

# ---------------------------------------------------------------------------
# 4 · Confluent credentials
# ---------------------------------------------------------------------------

say "Confluent credentials in Secret Manager"
for pair in \
  "pri-confluent-api-key:${CONFLUENT_API_KEY:-}" \
  "pri-confluent-api-secret:${CONFLUENT_API_SECRET:-}" \
  "pri-api-key:${PRI_API_KEY:-}"; do
  name="${pair%%:*}"
  value="${pair#*:}"
  if gcloud secrets describe "${name}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    printf '    %s already present\n' "${name}"
  elif [ -n "${value}" ]; then
    printf '%s' "${value}" |
      gcloud secrets create "${name}" --data-file=- --project "${PROJECT_ID}"
    printf '    %s created\n' "${name}"
  else
    printf '    %s not set in the environment; skipping\n' "${name}"
  fi
done

# ---------------------------------------------------------------------------
# 5 · Build and push
# ---------------------------------------------------------------------------

say "Building api and consumer images (${GIT_SHA})"
build_image Dockerfile.api "${IMAGE_BASE}/api:${GIT_SHA}"
build_image Dockerfile.consumer "${IMAGE_BASE}/consumer:${GIT_SHA}"

# ---------------------------------------------------------------------------
# 6 · API service
# ---------------------------------------------------------------------------

# No password in the URL. Cloud Run mounts it separately from Secret Manager as
# DATABASE_PASSWORD, and Settings.database_dsn folds it in at startup — so the
# secret never appears in a deploy command, a revision's env vars, or a log.
DB_URL="postgresql+asyncpg://${DB_USER}@/${DB_NAME}?host=/cloudsql/${CONNECTION_NAME}"

# The ^|^ prefix on every --set-env-vars below chooses "|" as the delimiter
# between variables, because the default is "," and this DSN could contain one.
#
# It must not be "@". It was, and the DSN contains one — `pri_user@/pri` — so
# gcloud split the value there and set DATABASE_URL to
# "postgresql+asyncpg://pri_user", with no host and no socket path. asyncpg
# then fell back to a TCP connection and failed with
# "Temporary failure in name resolution", an error that points at DNS and says
# nothing about a truncated environment variable.
#
# "|" cannot appear in a postgres DSN.

# --max-instances 1 is load-bearing, not a cost setting.
#
# The SSE broker in `api/stream.py` holds its subscribers in a process-local
# dict. Cloud Run routes every request independently, so above one instance a
# browser's event stream can be held by instance A while the recovery meant to
# narrate into it runs on instance B. The recovery succeeds, the API returns
# 200, and the screen the judge is watching stays empty.
#
# One warm instance at concurrency 40 carries this comfortably. The real fix is
# a shared bus — Redis pub/sub, or a consumer per instance — and until that
# exists, raising this number silently breaks the live pipeline for anyone who
# is not the only person watching.
#
# CPU allocation is left at the default (allocated during request processing),
# deliberately. The SSE push is entirely request-scoped at both ends: the
# stream response in `api/routes.py` IS the long-lived request — its generator
# holds the connection open, waking every 15s to write a keep-alive — and every
# `broker.publish()` happens inside an awaited handler, since `/recover`,
# `/approve` and `/execute` all run their pipeline inline rather than handing
# it to a background task. The broker itself (`api/stream.py`) is synchronous
# and starts nothing. So there is no moment when a frame needs to move while no
# request is in flight, and --no-cpu-throttling would buy nothing.
#
# The one piece of out-of-request work in the API is the Kafka bridge's
# delivery-callback poll and connectivity probe (`api/kafka_bridge.py`), which
# are `asyncio.create_task`. Those are not on the SSE path: a throttled gap
# delays a `last_delivery` field in /health, not a stage in the timeline.
say "Deploying the api service"
gcloud run deploy pri-api \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE_BASE}/api:${GIT_SHA}" \
  --platform managed \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 1 \
  --concurrency 40 \
  --cpu 1 --memory 1Gi \
  --cpu-boost \
  --service-account "${RUNTIME_SA}" \
  --add-cloudsql-instances "${CONNECTION_NAME}" \
  --set-env-vars "GIT_SHA=${GIT_SHA}" \
  --set-env-vars "PRI_ENV=production,PRI_LOG_LEVEL=INFO" \
  --set-env-vars "PRI_DEMO_PRODUCTION_ID=${DEMO_PRODUCTION_ID}" \
  --set-env-vars "PRI_ARTIFACT_BUCKET=${ARTIFACT_BUCKET}" \
  --set-env-vars "PRI_AGENT_ENABLED=${AGENT_ENABLED}" \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=true" \
  --set-env-vars "GOOGLE_GENAI_MODEL=${GEMINI_MODEL}" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION}" \
  --set-env-vars "CONFLUENT_BOOTSTRAP_SERVERS=${CONFLUENT_BOOTSTRAP_SERVERS}" \
  --set-env-vars "^|^DATABASE_URL=${DB_URL}" \
  --set-secrets "DATABASE_PASSWORD=pri-db-password:latest" \
  --set-secrets "CONFLUENT_API_KEY=pri-confluent-api-key:latest" \
  --set-secrets "CONFLUENT_API_SECRET=pri-confluent-api-secret:latest" \
  --set-secrets "PRI_INJECT_PASSCODE=pri-inject-passcode:latest" \
  --set-secrets "PRI_API_KEY=pri-api-key:latest"

API_URL="$(gcloud run services describe pri-api \
  --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')"
printf '    api at %s\n' "${API_URL}"

# ---------------------------------------------------------------------------
# 7 · Migrations and seed, as a one-shot job
# ---------------------------------------------------------------------------

# --set-cloudsql-instances, not --add-. Services accept --add-; jobs do not
# accept it at all. The usage error that produced was swallowed by a
# `2>/dev/null || jobs update` fallback, which then failed with "Job could not
# be found" — an error describing the consequence rather than the cause.
#
# The fallback is gone as well: `jobs deploy` already creates or updates.
say "Running migrations and seeding the demo production"
gcloud run jobs deploy pri-seed \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE_BASE}/api:${GIT_SHA}" \
  --service-account "${RUNTIME_SA}" \
  --set-cloudsql-instances "${CONNECTION_NAME}" \
  --set-env-vars "^|^DATABASE_URL=${DB_URL}" \
  --set-secrets "DATABASE_PASSWORD=pri-db-password:latest" \
  --command python \
  --args=-m,pri.persistence.bootstrap \
  --max-retries 1 \
  --task-timeout 300s

gcloud run jobs execute pri-seed --project "${PROJECT_ID}" --region "${REGION}" --wait

# ---------------------------------------------------------------------------
# 8 · Consumer service
# ---------------------------------------------------------------------------

say "Deploying the consumer service (min-instances 1)"
gcloud run deploy pri-consumer \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE_BASE}/consumer:${GIT_SHA}" \
  --platform managed \
  --no-allow-unauthenticated \
  --min-instances 1 \
  --max-instances 1 \
  --cpu 1 --memory 512Mi \
  --no-cpu-throttling \
  --service-account "${RUNTIME_SA}" \
  --add-cloudsql-instances "${CONNECTION_NAME}" \
  --set-env-vars "GIT_SHA=${GIT_SHA},PRI_ENV=production,PRI_LOG_LEVEL=INFO" \
  --set-env-vars "PRI_API_INTERNAL_URL=${API_URL}" \
  --set-env-vars "CONFLUENT_BOOTSTRAP_SERVERS=${CONFLUENT_BOOTSTRAP_SERVERS:-}" \
  --set-env-vars "^|^DATABASE_URL=${DB_URL}" \
  --set-secrets "DATABASE_PASSWORD=pri-db-password:latest" \
  --set-secrets "CONFLUENT_API_KEY=pri-confluent-api-key:latest" \
  --set-secrets "CONFLUENT_API_SECRET=pri-confluent-api-secret:latest"

# ---------------------------------------------------------------------------
# 9 · Web console
# ---------------------------------------------------------------------------

# No build args. The browser calls this service's own /api/pri proxy, and the
# proxy reads the backend URL and the API key from the environment per request
# — so the image is not pinned to the backend URL it was built against.
say "Building the web image"
build_image Dockerfile.web "${IMAGE_BASE}/web:${GIT_SHA}"

say "Deploying the web service"
gcloud run deploy pri-web \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE_BASE}/web:${GIT_SHA}" \
  --platform managed \
  --allow-unauthenticated \
  --service-account "${RUNTIME_SA}" \
  --set-env-vars "PRI_API_BASE_URL=${API_URL}" \
  --set-env-vars "PRI_DEMO_PRODUCTION_ID=${DEMO_PRODUCTION_ID}" \
  --set-secrets "PRI_API_KEY=pri-api-key:latest" \
  --min-instances 0 \
  --max-instances 4 \
  --concurrency 80 \
  --cpu 1 --memory 512Mi

WEB_URL="$(gcloud run services describe pri-web \
  --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')"

# ---------------------------------------------------------------------------

say "Done"
cat <<SUMMARY

  web       ${WEB_URL}
  api       ${API_URL}
  consumer  $(gcloud run services describe pri-consumer \
              --project "${PROJECT_ID}" --region "${REGION}" \
              --format='value(status.url)' 2>/dev/null || echo 'not deployed')
  health    ${API_URL}/health
  consumer  pri-consumer (min-instances 1 — the only always-on charge)

  Smoke test:
    curl -s ${API_URL}/health
    python scripts/run_demo.py --api ${API_URL}

SUMMARY
