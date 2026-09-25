-- ChainLens SQLite schema.
--
-- Boundary rule: source_rows.raw_json is the restricted archive of exactly what was
-- uploaded (including any evaluation label column). Every analysis consumer reads the
-- sanitised tables (transactions / tx_io / observations) which never carry labels.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cases (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT 'draft',   -- draft|analysing|ready_for_review|archived
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasets (
    id               TEXT PRIMARY KEY,
    case_id          TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    original_name    TEXT NOT NULL,
    archive_path     TEXT NOT NULL,
    sha256           TEXT NOT NULL,
    byte_size        INTEGER NOT NULL,
    detected_format  TEXT NOT NULL,                -- csv|json|xml
    validation_mode  TEXT NOT NULL,                -- compatibility|strict
    imported_at      TEXT NOT NULL,
    total_rows       INTEGER NOT NULL DEFAULT 0,
    accepted_clean   INTEGER NOT NULL DEFAULT 0,
    accepted_warning INTEGER NOT NULL DEFAULT 0,
    quarantined      INTEGER NOT NULL DEFAULT 0,
    label_column_removed INTEGER NOT NULL DEFAULT 0,
    schema_json      TEXT NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL DEFAULT 'importing'
);
CREATE INDEX IF NOT EXISTS idx_datasets_case ON datasets(case_id);

-- Restricted source archive. Analysis code must not select raw_json.
CREATE TABLE IF NOT EXISTS source_rows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    row_number  INTEGER NOT NULL,
    raw_json    TEXT NOT NULL,
    status      TEXT NOT NULL,                     -- accepted|warning|quarantined
    UNIQUE(dataset_id, row_number)
);

CREATE TABLE IF NOT EXISTS validation_issues (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id    TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    source_row_id INTEGER REFERENCES source_rows(id) ON DELETE CASCADE,
    row_number    INTEGER,
    code          TEXT NOT NULL,
    severity      TEXT NOT NULL,                   -- warning|error
    field         TEXT,
    message       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issues_dataset ON validation_issues(dataset_id);
CREATE INDEX IF NOT EXISTS idx_issues_code ON validation_issues(dataset_id, code);

-- Sanitised canonical records. No label columns exist in this table by construction.
CREATE TABLE IF NOT EXISTS transactions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    dataset_id     TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    txid           TEXT NOT NULL,
    observed_at    TEXT NOT NULL,                  -- ISO-8601 UTC
    observed_ts    REAL NOT NULL,                  -- epoch seconds, for ordering
    fee_sats       INTEGER,
    total_in_sats  INTEGER NOT NULL,
    total_out_sats INTEGER NOT NULL,
    residual_sats  INTEGER NOT NULL,               -- in - out - fee (0 when conserved)
    input_count    INTEGER NOT NULL,
    output_count   INTEGER NOT NULL,
    script_type    TEXT,
    quality_status TEXT NOT NULL,                  -- clean|warning
    payload_hash   TEXT NOT NULL,                  -- detects conflicting payloads per txid
    source_row_id  INTEGER NOT NULL REFERENCES source_rows(id) ON DELETE CASCADE,
    UNIQUE(case_id, txid)
);
CREATE INDEX IF NOT EXISTS idx_tx_case_time ON transactions(case_id, observed_ts);

CREATE TABLE IF NOT EXISTS tx_io (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    case_id        TEXT NOT NULL,
    side           TEXT NOT NULL,                  -- input|output
    position       INTEGER NOT NULL,               -- preserved array index
    address        TEXT NOT NULL,
    amount_sats    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_txio_tx ON tx_io(transaction_id);
CREATE INDEX IF NOT EXISTS idx_txio_addr ON tx_io(case_id, address);

-- Network observations are stored separately from spending relationships on purpose.
CREATE TABLE IF NOT EXISTS observations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    case_id        TEXT NOT NULL,
    source_row_id  INTEGER NOT NULL,
    observed_at    TEXT NOT NULL,
    observed_ts    REAL NOT NULL,
    src_ip         TEXT,
    src_port       INTEGER,
    dst_ip         TEXT,
    dst_port       INTEGER,
    UNIQUE(transaction_id, source_row_id)
);
CREATE INDEX IF NOT EXISTS idx_obs_src ON observations(case_id, src_ip);

CREATE TABLE IF NOT EXISTS runs (
    id               TEXT PRIMARY KEY,
    case_id          TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    dataset_id       TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    status           TEXT NOT NULL,                -- queued|running|complete|failed|cancelled
    stage            TEXT NOT NULL DEFAULT 'queued',
    progress         REAL NOT NULL DEFAULT 0.0,
    config_json      TEXT NOT NULL DEFAULT '{}',
    detector_version TEXT NOT NULL DEFAULT '',
    model_version    TEXT,
    model_status     TEXT NOT NULL DEFAULT 'unknown', -- used|unavailable
    dataset_sha256   TEXT NOT NULL DEFAULT '',
    started_at       TEXT,
    finished_at      TEXT,
    error            TEXT,
    stats_json       TEXT NOT NULL DEFAULT '{}',
    predictions_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_case ON runs(case_id);

CREATE TABLE IF NOT EXISTS address_scores (
    run_id             TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    address            TEXT NOT NULL,
    anomaly_score      REAL,                       -- higher = more unusual; NULL = unscored
    anomaly_percentile REAL,                       -- 0..100 against frozen reference
    scored             INTEGER NOT NULL DEFAULT 1,
    unscored_reason    TEXT,
    features_json      TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, address)
);

CREATE TABLE IF NOT EXISTS alerts (
    id                  TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    case_id             TEXT NOT NULL,
    subject_type        TEXT NOT NULL,             -- address|transaction
    subject_id          TEXT NOT NULL,
    pattern             TEXT NOT NULL,             -- detector key
    pattern_label       TEXT NOT NULL,             -- analyst-facing wording
    role_hypothesis     TEXT NOT NULL DEFAULT 'unknown_role',
    priority            REAL NOT NULL,
    priority_band       TEXT NOT NULL,             -- high|medium|low
    priority_components TEXT NOT NULL DEFAULT '{}',
    evidence_strength   TEXT NOT NULL,             -- low|medium|high
    evidence_reasons    TEXT NOT NULL DEFAULT '[]',
    period_start        TEXT,
    period_end          TEXT,
    explanation         TEXT NOT NULL,
    alternatives        TEXT NOT NULL DEFAULT '[]',
    caveats             TEXT NOT NULL DEFAULT '[]',
    indicators          TEXT NOT NULL DEFAULT '{}',
    review_state        TEXT NOT NULL DEFAULT 'new',
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_run ON alerts(run_id, priority DESC, id);
CREATE INDEX IF NOT EXISTS idx_alerts_subject ON alerts(run_id, subject_id);

CREATE TABLE IF NOT EXISTS alert_evidence (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id      TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,                   -- transaction|observation|derived
    txid          TEXT,
    source_row_id INTEGER,
    row_number    INTEGER,
    detail_json   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_evidence_alert ON alert_evidence(alert_id);

CREATE TABLE IF NOT EXISTS cluster_hypotheses (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    members_json  TEXT NOT NULL,
    edges_json    TEXT NOT NULL,
    rule_version  TEXT NOT NULL,
    strength      REAL NOT NULL DEFAULT 0,
    exceptions    TEXT NOT NULL DEFAULT '[]',
    decision      TEXT NOT NULL DEFAULT 'open',    -- open|accepted|rejected
    decision_note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id   TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    author     TEXT NOT NULL DEFAULT 'analyst',
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id   TEXT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    actor      TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state   TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT,
    actor       TEXT NOT NULL DEFAULT 'local',
    action      TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exports (
    id         TEXT PRIMARY KEY,
    case_id    TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    run_id     TEXT NOT NULL,
    directory  TEXT NOT NULL,
    files_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
