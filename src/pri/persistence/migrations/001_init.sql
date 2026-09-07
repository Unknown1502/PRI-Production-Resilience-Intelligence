-- 001_init.sql  — PRI initial schema
-- Append-only by design: no UPDATE/DELETE on state_versions.
-- All timestamps are stored as TIMESTAMPTZ (UTC).

-- ---------------------------------------------------------------------------
-- Core production catalogue
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS productions (
    id          TEXT        PRIMARY KEY,
    title       TEXT        NOT NULL,
    currency    TEXT        NOT NULL,
    shoot_start DATE        NOT NULL,
    shoot_end   DATE        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Disruption events  (idempotency key = event_id)
-- Must be created before state_versions which references it.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT        PRIMARY KEY,
    production_id TEXT        NOT NULL REFERENCES productions(id),
    event_type    TEXT        NOT NULL,
    occurred_at   TIMESTAMPTZ NOT NULL,
    source        TEXT        NOT NULL,
    severity      REAL        NOT NULL,
    payload       JSONB       NOT NULL DEFAULT '{}',
    consumed_at   TIMESTAMPTZ,
    UNIQUE (event_id)
);
-- ---------------------------------------------------------------------------
-- Immutable state snapshots
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS state_versions (
    production_id  TEXT        NOT NULL REFERENCES productions(id),
    version        INTEGER     NOT NULL CHECK (version >= 1),
    parent_version INTEGER,
    event_id       TEXT,
    snapshot       JSONB       NOT NULL,
    digest         TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (production_id, version),
    CONSTRAINT fk_sv_event FOREIGN KEY (event_id) REFERENCES events(event_id)
        DEFERRABLE INITIALLY DEFERRED
);

-- Fast lookup of the latest version and range scans.
CREATE INDEX IF NOT EXISTS idx_sv_prod_version_desc
    ON state_versions (production_id, version DESC);

CREATE INDEX IF NOT EXISTS idx_events_prod_occurred
    ON events (production_id, occurred_at);

-- ---------------------------------------------------------------------------
-- Recovery workflow
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS recovery_sessions (
    id            TEXT        PRIMARY KEY,
    production_id TEXT        NOT NULL REFERENCES productions(id),
    event_id      TEXT        REFERENCES events(event_id),
    base_version  INTEGER     NOT NULL,
    status        TEXT        NOT NULL
                              CHECK (status IN ('OPEN','APPROVED','REJECTED','EXPIRED')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS candidate_plans (
    id            TEXT        PRIMARY KEY,
    session_id    TEXT        NOT NULL REFERENCES recovery_sessions(id),
    label         TEXT        NOT NULL,
    moves         JSONB       NOT NULL DEFAULT '[]',
    valid         BOOLEAN     NOT NULL,
    violations    JSONB       NOT NULL DEFAULT '[]',
    score         JSONB,
    pareto_optimal BOOLEAN    NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT        PRIMARY KEY,
    session_id  TEXT        NOT NULL REFERENCES recovery_sessions(id),
    plan_id     TEXT        NOT NULL REFERENCES candidate_plans(id),
    approver    TEXT        NOT NULL,
    decision    TEXT        NOT NULL
                            CHECK (decision IN ('APPROVED','REJECTED')),
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note        TEXT
);

-- ---------------------------------------------------------------------------
-- Audit log  (append-only, ordered by at)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL   PRIMARY KEY,
    production_id TEXT        NOT NULL REFERENCES productions(id),
    at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor         TEXT        NOT NULL,
    action        TEXT        NOT NULL,
    subject       TEXT        NOT NULL,
    detail        JSONB       NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------------------
-- Generated artifacts (PDFs, reports, etc.)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS artifacts (
    id            TEXT        PRIMARY KEY,
    production_id TEXT        NOT NULL REFERENCES productions(id),
    version       INTEGER     NOT NULL,
    kind          TEXT        NOT NULL,
    path          TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
