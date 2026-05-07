-- AI Stock Recommender Database Schema
-- Phase 2: Core Table Design

-- Migration: Update raw_signals table structure (only if table exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='raw_signals') THEN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='raw_signals' AND column_name='source_name') THEN
            ALTER TABLE raw_signals ADD COLUMN source_name VARCHAR(100);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='raw_signals' AND column_name='source_tier') THEN
            ALTER TABLE raw_signals ADD COLUMN source_tier INT;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='raw_signals' AND column_name='url') THEN
            ALTER TABLE raw_signals ADD COLUMN url TEXT;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='raw_signals' AND column_name='content_hash') THEN
            ALTER TABLE raw_signals ADD COLUMN content_hash VARCHAR(64);
            ALTER TABLE raw_signals ADD CONSTRAINT raw_signals_content_hash_key UNIQUE (content_hash);
        END IF;
    END IF;
END $$;

-- Migration: Update extracted_recommendations table structure (only if table exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='extracted_recommendations') THEN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='extracted_recommendations' AND column_name='source_name') THEN
            ALTER TABLE extracted_recommendations ADD COLUMN source_name VARCHAR(100);
        END IF;
    END IF;
END $$;

-- Feed sources configuration table (dynamic management)
CREATE TABLE IF NOT EXISTS feed_sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    route TEXT NOT NULL,                  -- RSSHub route path
    category VARCHAR(50) NOT NULL,        -- research/news/kol/concept/lhb
    tier INT NOT NULL DEFAULT 2,          -- 1=high quality, 2=medium, 3=low
    weight FLOAT NOT NULL DEFAULT 1.0,    -- dynamic weight (IC feedback)
    poll_interval_sec INT DEFAULT 600,    -- polling interval
    enabled BOOLEAN DEFAULT TRUE,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error_msg TEXT,
    consecutive_failures INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_feed_enabled ON feed_sources(enabled, poll_interval_sec);

-- Raw signals table (each RSS entry lands here)
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
    ts_code VARCHAR(20) NOT NULL,         -- 600519.SH
    stock_name VARCHAR(50),
    recommendation_type VARCHAR(30),      -- buy/watch/strong_buy/sell
    strength INT,                         -- 1-5
    logic_category VARCHAR(50),           -- 题材/业绩/技术/资金
    logic_summary TEXT,                   -- LLM extracted core logic
    target_price NUMERIC(10,3),
    stop_loss NUMERIC(10,3),
    time_horizon VARCHAR(20),             -- intraday/overnight/weekly/monthly
    raw_excerpt TEXT,                     -- original excerpt
    confidence FLOAT,                     -- LLM extraction confidence
    pub_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_extr_ts_code ON extracted_recommendations(ts_code, pub_time DESC);
CREATE INDEX idx_extr_pub_time ON extracted_recommendations(pub_time DESC);

-- Daily candidate pool snapshot
CREATE TABLE IF NOT EXISTS daily_candidates (
    id BIGSERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    ts_code VARCHAR(20) NOT NULL,
    stock_name VARCHAR(50),
    mention_count INT,                    -- times mentioned across web
    source_diversity INT,                 -- number of source types
    consensus_score FLOAT,                -- consensus score
    llm_score FLOAT,
    quant_score FLOAT,
    final_score FLOAT,
    logic_tags TEXT[],
    selected BOOLEAN DEFAULT FALSE,       -- finally selected
    position_pct FLOAT,
    entry_low NUMERIC(10,3),
    entry_high NUMERIC(10,3),
    stop_loss NUMERIC(10,3),
    target_1 NUMERIC(10,3),
    target_2 NUMERIC(10,3),
    sources JSONB,                        -- source details list
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(snapshot_date, ts_code)
);

CREATE INDEX idx_cand_date ON daily_candidates(snapshot_date DESC);
CREATE INDEX idx_cand_selected ON daily_candidates(selected, snapshot_date DESC);

-- Push history
CREATE TABLE IF NOT EXISTS push_history (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT REFERENCES daily_candidates(id),
    push_type VARCHAR(30),                -- pre_open/open_15min/intraday_alert
    chat_id VARCHAR(50),
    message_id BIGINT,
    pushed_at TIMESTAMPTZ DEFAULT NOW(),
    user_action VARCHAR(20),              -- watch/ignore/entered
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
    t5_close NUMERIC(10,3),
    t5_return FLOAT,
    t20_close NUMERIC(10,3),
    t20_return FLOAT,
    hit_target BOOLEAN,
    hit_stop BOOLEAN,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Source health monitoring (for dynamic weight adjustment)
CREATE TABLE IF NOT EXISTS source_performance (
    id BIGSERIAL PRIMARY KEY,
    source_id INT REFERENCES feed_sources(id),
    eval_date DATE NOT NULL,
    rolling_window_days INT DEFAULT 60,
    rec_count INT,
    avg_t1_return FLOAT,
    avg_t5_return FLOAT,
    win_rate FLOAT,
    ic_value FLOAT,                       -- correlation coefficient with actual returns
    UNIQUE(source_id, eval_date)
);
