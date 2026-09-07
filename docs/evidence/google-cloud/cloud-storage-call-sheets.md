# Cloud Storage, called at runtime

Evidence that issued call sheets are written to Cloud Storage by the deployed
service, not by a script on a laptop. Captured 2026-09-07, against
`https://pri-web-3r5eyp275a-uc.a.run.app`, revision `pri-api-00015-vqf`,
`git_sha ec06a15`.

## Why this exists

Call sheets were written to container-local disk while the `artifacts` row
recording the issue went to Postgres. Cloud Run replaces the container on every
revision, so the row outlived the document: the audit trail claimed a
publication it could not produce. For a system whose argument is a defensible
record, that is the wrong half to lose.

## What was driven

A real disruption through the deployed API, then the governance path:

1. `POST /api/productions/film-001/events` — the LOC-04 blockage. `200`.
2. `POST /api/productions/film-001/recover` — returned `mode: agent`, four
   candidates, frontier `{plan-A, plan-B2}`, `plan-B` refused by C001.
3. `POST /api/sessions/rec-7f25f8efc199/execute` **before** approving —
   refused, as it should be:

   ```json
   {"error": "approval_missing",
    "detail": "No approval recorded for plan B2 in session rec-7f25f8efc199",
    "step": "request_approval"}
   ```

4. `POST /api/sessions/rec-7f25f8efc199/approve` — `APPROVED`, approval id
   `b263d953-ee11-4618-8d8d-a8afb37d69fa`.
5. `POST /api/sessions/rec-7f25f8efc199/execute` — the seven-step transition,
   version 1 → 2.

## What the transition returned

Seven artifacts, each a `gs://` URI rather than a container path:

```
gs://pri-production-resilience-call-sheets/call-sheets/film-001/v2/2026-09-08.pdf
gs://pri-production-resilience-call-sheets/call-sheets/film-001/v2/2026-09-09.pdf
gs://pri-production-resilience-call-sheets/call-sheets/film-001/v2/2026-09-10.pdf
gs://pri-production-resilience-call-sheets/call-sheets/film-001/v2/2026-09-11.pdf
gs://pri-production-resilience-call-sheets/call-sheets/film-001/v2/2026-09-12.pdf
gs://pri-production-resilience-call-sheets/call-sheets/film-001/v2/2026-09-15.pdf
gs://pri-production-resilience-call-sheets/call-sheets/film-001/v2/2026-09-17.pdf
```

## What the bucket holds

`gcloud storage ls -l gs://pri-production-resilience-call-sheets/call-sheets/film-001/v2/`

```
      3376  2026-09-07T14:18:10Z  .../2026-09-08.pdf
      3475  2026-09-07T14:18:10Z  .../2026-09-09.pdf
      3494  2026-09-07T14:18:10Z  .../2026-09-10.pdf
      3580  2026-09-07T14:18:10Z  .../2026-09-11.pdf
      3380  2026-09-07T14:18:11Z  .../2026-09-12.pdf
      3243  2026-09-07T14:18:11Z  .../2026-09-15.pdf
      3249  2026-09-07T14:18:11Z  .../2026-09-17.pdf
TOTAL: 7 objects, 23797 bytes
```

`gcloud storage objects describe .../2026-09-10.pdf` → `application/pdf`, 3494
bytes. Real PDFs, correct content type, written by the Cloud Run service under
its own runtime service account.

## Where the code is

[`src/pri/artifacts/store.py`](../../../src/pri/artifacts/store.py) —
`from google.cloud import storage`, `blob.upload_from_string(...)`. Selected by
`PRI_ARTIFACT_BUCKET`, which [`infra/deploy.sh`](../../../infra/deploy.sh) sets
on `pri-api`. No bucket configured means the local filesystem, so a checkout
still renders a PDF without a cloud account.

The bucket has uniform bucket-level access and public access prevention; the
runtime service account holds `roles/storage.objectAdmin` on that bucket alone,
not project-wide. A call sheet carries a unit's addresses and cast call times.

## Afterwards

`POST /api/demo/reset` returned film-001 to version 1, so the demo starts
clean. The v2 objects are left in the bucket as evidence.
