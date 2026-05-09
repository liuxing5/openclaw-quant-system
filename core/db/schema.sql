-- AI Stock Recommender Database Schema
-- Fully idempotent - safe to run multiple times

-- Feed sources configuration table
CREATE TABLE IF NOT EXISTS feed_sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    route TEXT NOT NULL,
    category VARCHAR(50) NOT NULL,
    tier INT NOT NULL DEFAULT 2,
    weight FLOAT NOT NULL DEFAULT 1.0,
    poll_interval_sec INT DEFAULT 600,
    enabled BOOLEAN DEFAULT TRUE,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error_msg TEXT,
    consecutive_failures INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feed_enabled ON feed_sources(enabled, poll_interval_sec);

-- Raw signals table
CREATE TABLE IF NOT EXISTS raw_signals (
    id BIGSERIAL PRIMARY KEY,
    source_id INT,
    source_name VARCHAR(100),
    source_tier INT,
    title TEXT,
    content TEXT,
    url TEXT,
    pub_time TIMESTAMPTZ,
    fetch_time TIMESTAMPTZ DEFAULT NOW(),
    content_hash VARCHAR(64) UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_raw_pub_time ON raw_signals(pub_time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_fetch_time ON raw_signals(fetch_time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_signals(source_id);

-- Extracted structured recommendations
CREATE TABLE IF NOT EXISTS extracted_recommendations (
    id BIGSERIAL PRIMARY KEY,
    raw_signal_id BIGINT REFERENCES raw_signals(id),
    source_name VARCHAR(100),
    ts_code VARCHAR(20) NOT NULL,
    stock_name VARCHAR(50),
    recommendation_type VARCHAR(30),
    strength INT,
    logic_category VARCHAR(50),
    logic_summary TEXT,
    target_price NUMERIC(10,3),
    stop_loss NUMERIC(10,3),
    time_horizon VARCHAR(20),
    raw_excerpt TEXT,
    confidence FLOAT,
    pub_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_extr_ts_code ON extracted_recommendations(ts_code, pub_time DESC);
CREATE INDEX IF NOT EXISTS idx_extr_pub_time ON extracted_recommendations(pub_time DESC);

-- Daily candidate pool snapshot
CREATE TABLE IF NOT EXISTS daily_candidates (
    id BIGSERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    ts_code VARCHAR(20) NOT NULL,
    stock_name VARCHAR(50),
    mention_count INT,
    source_diversity INT,
    consensus_score FLOAT,
    llm_score FLOAT,
    quant_score FLOAT,
    final_score FLOAT,
    logic_tags TEXT[],
    selected BOOLEAN DEFAULT FALSE,
    position_pct FLOAT,
    entry_low NUMERIC(10,3),
    entry_high NUMERIC(10,3),
    stop_loss NUMERIC(10,3),
    target_1 NUMERIC(10,3),
    target_2 NUMERIC(10,3),
    sources JSONB,
    run_mode VARCHAR(20) DEFAULT 'afternoon',
    source VARCHAR(30) NOT NULL DEFAULT 'llm_multisource',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT daily_candidates_unique_source UNIQUE(snapshot_date, ts_code, run_mode, source)
);

CREATE INDEX IF NOT EXISTS idx_cand_date ON daily_candidates(snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_cand_selected ON daily_candidates(selected, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_cand_source ON daily_candidates(source, snapshot_date DESC);

-- Push history
CREATE TABLE IF NOT EXISTS push_history (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT REFERENCES daily_candidates(id),
    push_type VARCHAR(30),
    chat_id VARCHAR(50),
    message_id BIGINT,
    pushed_at TIMESTAMPTZ DEFAULT NOW(),
    user_action VARCHAR(20),
    user_action_at TIMESTAMPTZ
);

-- Live tracking (T+N review)
CREATE TABLE IF NOT EXISTS performance_tracking (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT REFERENCES daily_candidates(id),
    ts_code VARCHAR(20),
    rec_date DATE,
    entry_price NUMERIC(10,3),
    t1_high NUMERIC(10,3),
    t1_low NUMERIC(10,3),
    t1_close NUMERIC(10,3),
    t1_return FLOAT,
    t1_hit_target BOOLEAN,
    t1_hit_stop BOOLEAN,
    t5_high NUMERIC(10,3),
    t5_low NUMERIC(10,3),
    t5_close NUMERIC(10,3),
    t5_return FLOAT,
    t5_hit_target BOOLEAN,
    t5_hit_stop BOOLEAN,
    t20_high NUMERIC(10,3),
    t20_low NUMERIC(10,3),
    t20_close NUMERIC(10,3),
    t20_return FLOAT,
    t20_hit_target BOOLEAN,
    t20_hit_stop BOOLEAN,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(candidate_id, rec_date)
);

-- Source health monitoring
CREATE TABLE IF NOT EXISTS source_performance (
    id BIGSERIAL PRIMARY KEY,
    source_id INT REFERENCES feed_sources(id),
    eval_date DATE NOT NULL,
    rolling_window_days INT DEFAULT 60,
    rec_count INT,
    avg_t1_return FLOAT,
    avg_t5_return FLOAT,
    win_rate FLOAT,
    ic_value FLOAT,
    UNIQUE(source_id, eval_date)
);

-- Market data tables
CREATE TABLE IF NOT EXISTS daily_quotes (
    ts_code VARCHAR(20),
    trade_date DATE,
    open NUMERIC(10,3), high NUMERIC(10,3), low NUMERIC(10,3), close NUMERIC(10,3),
    volume BIGINT, amount NUMERIC(20,2),
    pct_chg FLOAT, turnover_rate FLOAT,
    PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS lhb_detail (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE, ts_code VARCHAR(20), stock_name VARCHAR(50),
    reason TEXT, buy_amt NUMERIC(20,2), sell_amt NUMERIC(20,2),
    net_amt NUMERIC(20,2), is_inst BOOLEAN,
    CONSTRAINT lhb_unique UNIQUE (trade_date, ts_code, reason)
);

CREATE INDEX IF NOT EXISTS idx_lhb_date ON lhb_detail(trade_date DESC, ts_code);

CREATE TABLE IF NOT EXISTS hsgt_individual (
    ts_code VARCHAR(20), trade_date DATE,
    hold_shares BIGINT, hold_market_cap NUMERIC(20,2),
    net_buy_amount NUMERIC(20,2),
    PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS concept_membership (
    ts_code VARCHAR(20), concept_code VARCHAR(20), concept_name VARCHAR(100),
    update_date DATE, PRIMARY KEY (ts_code, concept_code)
);

-- Stock basic info (full A-share code-name mapping, synced daily)
CREATE TABLE IF NOT EXISTS stock_basic_info (
    ts_code VARCHAR(20) PRIMARY KEY,
    stock_name VARCHAR(50) NOT NULL,
    market VARCHAR(10),
    list_date DATE,
    is_st BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stock_name ON stock_basic_info(stock_name);
CREATE INDEX IF NOT EXISTS idx_stock_active ON stock_basic_info(is_active);
