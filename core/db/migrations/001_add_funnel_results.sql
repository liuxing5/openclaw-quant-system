-- Migration: Add funnel_results table
-- Date: 2026-05-12

CREATE TABLE IF NOT EXISTS funnel_results (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    total_stocks INT,
    layer0_pass INT,
    layer0_max_position FLOAT,
    layer1_pass INT,
    layer2_pass INT,
    layer3_pass INT,
    layer4_pass INT,
    layer5_pass INT,
    layer6_pass INT,
    market_advancers INT,
    market_decliners INT,
    market_index_close NUMERIC(10,3),
    market_index_ema NUMERIC(10,3),
    elapsed_seconds FLOAT,
    candidates JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT funnel_unique_date UNIQUE (trade_date)
);

CREATE INDEX IF NOT EXISTS idx_funnel_date ON funnel_results(trade_date DESC);
