-- Main Uptrend daily run stats (for HTML report display)
CREATE TABLE IF NOT EXISTS main_uptrend_runs (
    id BIGSERIAL PRIMARY KEY,
    run_date DATE NOT NULL,
    a_pool_size INT DEFAULT 0,
    b_signals INT DEFAULT 0,
    c_signals INT DEFAULT 0,
    d_passed INT DEFAULT 0,
    candidates INT DEFAULT 0,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT main_uptrend_runs_unique UNIQUE (run_date)
);
CREATE INDEX IF NOT EXISTS idx_uptrend_runs_date ON main_uptrend_runs(run_date DESC);
