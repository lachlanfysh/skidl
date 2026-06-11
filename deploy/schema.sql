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
