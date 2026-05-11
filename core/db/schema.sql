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

-- Layer 1: Extend daily_quotes with Tencent supplementary fields
ALTER TABLE daily_quotes ADD COLUMN IF NOT EXISTS pe_ratio FLOAT;
ALTER TABLE daily_quotes ADD COLUMN IF NOT EXISTS pb_ratio FLOAT;
ALTER TABLE daily_quotes ADD COLUMN IF NOT EXISTS total_market_cap NUMERIC(20,2);
ALTER TABLE daily_quotes ADD COLUMN IF NOT EXISTS circulating_market_cap NUMERIC(20,2);
ALTER TABLE daily_quotes ADD COLUMN IF NOT EXISTS limit_up_price NUMERIC(10,3);
ALTER TABLE daily_quotes ADD COLUMN IF NOT EXISTS limit_down_price NUMERIC(10,3);
ALTER TABLE daily_quotes ADD COLUMN IF NOT EXISTS amplitude FLOAT;
ALTER TABLE daily_quotes ADD COLUMN IF NOT EXISTS volume_ratio FLOAT;
ALTER TABLE daily_quotes ADD COLUMN IF NOT EXISTS commission_ratio FLOAT;
ALTER TABLE daily_quotes ADD COLUMN IF NOT EXISTS large_order_net FLOAT;
ALTER TABLE daily_quotes ADD COLUMN IF NOT EXISTS main_force_net NUMERIC(20,2);

-- Layer 1: Order book snapshots (mootdx, intraday only)
CREATE TABLE IF NOT EXISTS order_book_snapshot (
    id BIGSERIAL PRIMARY KEY,
    ts_code VARCHAR(20) NOT NULL,
    snapshot_time TIMESTAMPTZ NOT NULL,
    bid1_price NUMERIC(10,3), bid1_vol INT,
    bid2_price NUMERIC(10,3), bid2_vol INT,
    bid3_price NUMERIC(10,3), bid3_vol INT,
    bid4_price NUMERIC(10,3), bid4_vol INT,
    bid5_price NUMERIC(10,3), bid5_vol INT,
    ask1_price NUMERIC(10,3), ask1_vol INT,
    ask2_price NUMERIC(10,3), ask2_vol INT,
    ask3_price NUMERIC(10,3), ask3_vol INT,
    ask4_price NUMERIC(10,3), ask4_vol INT,
    ask5_price NUMERIC(10,3), ask5_vol INT,
    CONSTRAINT order_book_unique UNIQUE (ts_code, snapshot_time)
);
CREATE INDEX IF NOT EXISTS idx_ob_ts ON order_book_snapshot(ts_code, snapshot_time DESC);

-- Layer 1: Strong stock rankings (THS 同花顺强势股)
CREATE TABLE IF NOT EXISTS strong_stock_rank (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    ts_code VARCHAR(20) NOT NULL,
    stock_name VARCHAR(50),
    rank_type VARCHAR(20) NOT NULL,  -- 'lxsz'=连续上涨, 'cxg'=创新高, 'ljqd'=量价齐升
    rank_position INT,               -- 排名位置
    consecutive_days INT,            -- 连续上涨天数
    stage_chg_pct FLOAT,             -- 阶段涨跌幅
    cumulative_turnover FLOAT,       -- 累计换手率
    industry VARCHAR(50),
    latest_price NUMERIC(10,3),
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT strong_rank_unique UNIQUE (trade_date, ts_code, rank_type)
);
CREATE INDEX IF NOT EXISTS idx_strong_date ON strong_stock_rank(trade_date DESC, rank_type);
CREATE INDEX IF NOT EXISTS idx_strong_code ON strong_stock_rank(ts_code, trade_date DESC);

-- Layer 2: Earnings forecast (机构一致预期 EPS)
CREATE TABLE IF NOT EXISTS earnings_forecast (
    id BIGSERIAL PRIMARY KEY,
    ts_code VARCHAR(20) NOT NULL,
    stock_name VARCHAR(50),
    forecast_year INT NOT NULL,      -- 预测年度
    institution_count INT,           -- 预测机构数
    eps_min NUMERIC(10,4),           -- 最小值
    eps_mean NUMERIC(10,4),          -- 均值
    eps_max NUMERIC(10,4),           -- 最大值
    industry_avg NUMERIC(10,4),      -- 行业平均数
    revenue_mean NUMERIC(20,2),      -- 营收预测均值
    profit_mean NUMERIC(20,2),       -- 利润预测均值
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT forecast_unique UNIQUE (ts_code, forecast_year)
);
CREATE INDEX IF NOT EXISTS idx_fc_code ON earnings_forecast(ts_code, forecast_year DESC);
CREATE INDEX IF NOT EXISTS idx_fc_year ON earnings_forecast(forecast_year DESC);

-- Layer 1: Concept board quotes (概念板块行情)
CREATE TABLE IF NOT EXISTS concept_board_quotes (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    concept_code VARCHAR(20) NOT NULL,
    concept_name VARCHAR(100) NOT NULL,
    pct_chg FLOAT,
    turnover_rate FLOAT,
    lead_stock_code VARCHAR(20),
    lead_stock_name VARCHAR(50),
    stock_count INT,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT concept_quote_unique UNIQUE (trade_date, concept_code)
);
CREATE INDEX IF NOT EXISTS idx_concept_date ON concept_board_quotes(trade_date DESC, pct_chg DESC);
CREATE INDEX IF NOT EXISTS idx_concept_code ON concept_board_quotes(concept_code, trade_date DESC);

-- Layer 4: Stock fundamentals (mootdx quarterly data)
CREATE TABLE IF NOT EXISTS stock_fundamentals (
    ts_code VARCHAR(20) NOT NULL,
    report_date DATE NOT NULL,
    revenue NUMERIC(20,2),
    net_profit NUMERIC(20,2),
    net_profit_deducted NUMERIC(20,2),
    gross_margin FLOAT,
    net_margin FLOAT,
    total_assets NUMERIC(20,2),
    total_liabilities NUMERIC(20,2),
    equity NUMERIC(20,2),
    debt_ratio FLOAT,
    operating_cashflow NUMERIC(20,2),
    eps NUMERIC(10,4),
    bps NUMERIC(10,4),
    pe_ratio FLOAT,
    pb_ratio FLOAT,
    total_market_cap NUMERIC(20,2),
    circulating_market_cap NUMERIC(20,2),
    revenue_yoy FLOAT,
    profit_yoy FLOAT,
    industry VARCHAR(50),
    listing_date DATE,
    shareholder_count INT,
    top10_holder_pct FLOAT,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ts_code, report_date)
);
CREATE INDEX IF NOT EXISTS idx_fund_ts ON stock_fundamentals(ts_code, report_date DESC);

-- Layer 5: Announcements (巨潮 cninfo + mootdx)
CREATE TABLE IF NOT EXISTS stock_announcements (
    id BIGSERIAL PRIMARY KEY,
    ts_code VARCHAR(20),
    stock_name VARCHAR(50),
    title TEXT NOT NULL,
    category VARCHAR(50),
    publish_date DATE,
    url TEXT,
    content_hash VARCHAR(64) UNIQUE,
    source VARCHAR(50),
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ann_ts ON stock_announcements(ts_code, publish_date DESC);
