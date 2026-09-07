# PROMPT 16 — Containerise and Deploy to Cloud Run

> `[GATE]` The judges test the live URL between **Sep 23 and Oct 7** — it must
> survive a month unattended.

## Task

Make PRI publicly hosted.

## Create

| File | Purpose |
|---|---|
| `Dockerfile.api` | multi-stage, `python:3.12-slim`, non-root user, uvicorn |
| `Dockerfile.web` | `node:22-alpine` build then `next start`, non-root |
| `Dockerfile.consumer` | the Confluent runner |
| `.dockerignore` | for both build contexts |
| `infra/deploy.sh` | the gcloud commands (below) |
| `infra/README.md` | exact ordered commands, with the cost note that min-instances on the consumer is the only always-on charge |

## `infra/deploy.sh` must

1. Enable `run`, `sqladmin`, `secretmanager`, `artifactregistry` APIs
2. Create an Artifact Registry repo
3. Build and push all three images
4. Create a Cloud SQL Postgres instance (smallest tier) + database + user
5. Put DB password and Confluent key/secret into Secret Manager
6. Deploy `api` (min-instances 0, concurrency 40, CPU always-allocated off)
7. Deploy `web` with `NEXT_PUBLIC_API_URL` pointing at the api URL
8. Deploy `consumer` as a Cloud Run service with **min-instances 1**
9. Run migrations + seed as a one-shot Cloud Run job

## Reliability

- `/health` must verify **DB connectivity** and report the git sha
- **Cold-start protection**: the web app must render the overview from a cached
  snapshot if the API is waking up, never a spinner-of-death

## Acceptance Criteria

A stranger opening the public web URL in a clean browser, with no login, sees the
seeded production and can run the full disruption, approval and verification flow.

## Constraints

- DO NOT bake any secret into an image
- DO NOT commit a service-account key
