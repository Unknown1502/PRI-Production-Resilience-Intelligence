-- 002_import_staging.sql — uploaded workbooks, held for review.
--
-- An upload is never written straight to state_versions. It lands here, gets
-- parsed and audited against the constraint validator, and is committed only
-- when a human confirms. That is what makes "PRI accepted my broken schedule
-- and told me what was wrong with it" possible without also making
-- "PRI silently overwrote my board" possible.
--
-- The raw file is never stored. parsed_payload holds the ProductionState we
-- built from it and report holds the findings; the bytes themselves are
-- fingerprinted and discarded.

CREATE TABLE IF NOT EXISTS import_staging (
    id                TEXT        PRIMARY KEY,
    production_id     TEXT,
    filename          TEXT        NOT NULL,
    uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by       TEXT        NOT NULL,
    byte_size         INTEGER     NOT NULL,
    sha256            TEXT        NOT NULL,
    parsed_payload    JSONB,
    report            JSONB       NOT NULL,
    status            TEXT        NOT NULL
                                  CHECK (status IN ('PENDING_REVIEW','COMMITTED','REJECTED','FAILED')),
    committed_version INTEGER,
    expires_at        TIMESTAMPTZ
);

-- purge_expired() sweeps on exactly this pair.
CREATE INDEX IF NOT EXISTS idx_import_staging_status_expiry
    ON import_staging (status, expires_at);

-- Re-uploading the same file must return the existing review rather than
-- creating a second one. Partial, so a rejected upload can be retried after
-- the user fixes their process rather than the file.
CREATE UNIQUE INDEX IF NOT EXISTS idx_import_staging_pending_sha
    ON import_staging (sha256)
    WHERE status = 'PENDING_REVIEW';
