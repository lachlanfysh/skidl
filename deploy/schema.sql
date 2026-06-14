-- EDA MCP Server PostgreSQL Schema

-- Jobs (queue for async design generation)
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'queued',
    spec JSONB NOT NULL,
    options JSONB DEFAULT '{}',
    policy JSONB DEFAULT '{}',
    result JSONB,
    parent_job_id TEXT,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    worker_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_parent ON jobs(parent_job_id) WHERE parent_job_id IS NOT NULL;

-- Runs (replaces filesystem RunStore)
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id),
    spec JSONB NOT NULL,
    exceptions JSONB DEFAULT '[]',
    response JSONB,
    artifacts JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job_id) WHERE job_id IS NOT NULL;

-- Human visual/design feedback captured after an agent shows run artifacts.
CREATE TABLE IF NOT EXISTS run_feedback (
    id SERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    artifact TEXT DEFAULT '',
    source TEXT NOT NULL DEFAULT 'human_via_agent',
    feedback TEXT NOT NULL,
    structured JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_run_feedback_run ON run_feedback(run_id);
CREATE INDEX IF NOT EXISTS idx_run_feedback_created ON run_feedback(created_at);

-- Telemetry (replaces JSONL append)
CREATE TABLE IF NOT EXISTS telemetry (
    id SERIAL PRIMARY KEY,
    run_id TEXT,
    board_id TEXT,
    mode TEXT,
    status TEXT,
    geometry JSONB,
    cpu_time_s REAL,
    peak_rss_mb REAL,
    layout_score REAL,
    total_hpwl_mm REAL,
    congestion_score REAL,
    exceptions_raised JSONB DEFAULT '[]',
    record JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_telemetry_board ON telemetry(board_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_status ON telemetry(status);
CREATE INDEX IF NOT EXISTS idx_telemetry_created ON telemetry(created_at);

-- Open beta signup requests.
CREATE TABLE IF NOT EXISTS beta_signups (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL,
    email_normalized TEXT NOT NULL UNIQUE,
    name TEXT DEFAULT '',
    organization TEXT DEFAULT '',
    use_case TEXT DEFAULT '',
    source TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_beta_signups_status ON beta_signups(status);
CREATE INDEX IF NOT EXISTS idx_beta_signups_created ON beta_signups(created_at);

-- Beta users and per-user MCP API keys.
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL,
    email_normalized TEXT NOT NULL UNIQUE,
    name TEXT DEFAULT '',
    organization TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

CREATE TABLE IF NOT EXISTS api_keys (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL DEFAULT 'default',
    token_prefix TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_status ON api_keys(status);

-- Converted LCSC parts (easyeda2kicad cache)
CREATE TABLE IF NOT EXISTS converted_parts (
    lcsc TEXT PRIMARY KEY,
    sym_data BYTEA,
    fp_data BYTEA,
    step_data BYTEA,
    meta JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
