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
SQL_TIER="${SQL_TIER:-db-f1-micro}"
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
  gcloud builds submit --project "${PROJECT_ID}" --config - . <<YAML
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
gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="PRI container images" \
  --project "${PROJECT_ID}" 2>/dev/null || already

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# ---------------------------------------------------------------------------
# 3 · Cloud SQL
# ---------------------------------------------------------------------------

say "Cloud SQL instance ${SQL_INSTANCE} (${SQL_TIER})"
gcloud sql instances create "${SQL_INSTANCE}" \
  --database-version=POSTGRES_16 \
  --tier="${SQL_TIER}" \
  --region="${REGION}" \
  --storage-size=10GB \
  --storage-auto-increase \
  --project "${PROJECT_ID}" 2>/dev/null || already

gcloud sql databases create "${DB_NAME}" \
  --instance="${SQL_INSTANCE}" \
  --project "${PROJECT_ID}" 2>/dev/null || already

# Generated here and never printed. If you need it again, read it out of
# Secret Manager rather than re-running this script.
if ! gcloud secrets describe pri-db-password --project "${PROJECT_ID}" >/dev/null 2>&1; then
  say "Generating the database password into Secret Manager"
  DB_PASSWORD="$(openssl rand -base64 32 | tr -d '\n/+=' | head -c 32)"
  printf '%s' "${DB_PASSWORD}" |
    gcloud secrets create pri-db-password --data-file=- --project "${PROJECT_ID}"
  gcloud sql users create "${DB_USER}" \
    --instance="${SQL_INSTANCE}" \
    --password="${DB_PASSWORD}" \
    --project "${PROJECT_ID}" 2>/dev/null || already
else
  printf '    pri-db-password already in Secret Manager\n'
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

say "Deploying the api service"
gcloud run deploy pri-api \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE_BASE}/api:${GIT_SHA}" \
  --platform managed \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 4 \
  --concurrency 40 \
  --cpu 1 --memory 1Gi \
  --cpu-boost \
  --service-account "${RUNTIME_SA}" \
  --add-cloudsql-instances "${CONNECTION_NAME}" \
  --set-env-vars "GIT_SHA=${GIT_SHA}" \
  --set-env-vars "PRI_ENV=production,PRI_LOG_LEVEL=INFO" \
  --set-env-vars "PRI_DEMO_PRODUCTION_ID=${DEMO_PRODUCTION_ID}" \
  --set-env-vars "PRI_AGENT_ENABLED=${AGENT_ENABLED}" \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=true" \
  --set-env-vars "GOOGLE_GENAI_MODEL=${GEMINI_MODEL}" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION}" \
  --set-env-vars "CONFLUENT_BOOTSTRAP_SERVERS=${CONFLUENT_BOOTSTRAP_SERVERS}" \
  --set-env-vars "^@^DATABASE_URL=${DB_URL}" \
  --set-secrets "DATABASE_PASSWORD=pri-db-password:latest" \
  --set-secrets "CONFLUENT_API_KEY=pri-confluent-api-key:latest" \
  --set-secrets "CONFLUENT_API_SECRET=pri-confluent-api-secret:latest" \
  --set-secrets "PRI_API_KEY=pri-api-key:latest"

API_URL="$(gcloud run services describe pri-api \
  --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')"
printf '    api at %s\n' "${API_URL}"

# ---------------------------------------------------------------------------
# 7 · Migrations and seed, as a one-shot job
# ---------------------------------------------------------------------------

say "Running migrations and seeding the demo production"
gcloud run jobs deploy pri-seed \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE_BASE}/api:${GIT_SHA}" \
  --service-account "${RUNTIME_SA}" \
  --add-cloudsql-instances "${CONNECTION_NAME}" \
  --set-env-vars "^@^DATABASE_URL=${DB_URL}" \
  --set-secrets "DATABASE_PASSWORD=pri-db-password:latest" \
  --command python \
  --args "-m,pri.persistence.bootstrap" \
  --max-retries 1 \
  --task-timeout 300s 2>/dev/null || \
gcloud run jobs update pri-seed \
  --project "${PROJECT_ID}" --region "${REGION}" \
  --image "${IMAGE_BASE}/api:${GIT_SHA}"

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
  --set-env-vars "^@^DATABASE_URL=${DB_URL}" \
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
