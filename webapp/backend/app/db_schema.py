# -*- coding: utf-8 -*-
"""webapp 自有业务表（自选 / 持仓 / 提醒 / 模拟盘 / 反馈 / 策略 / 复盘 / 情绪）。

原库是只读的数据底座（daily_quotes、lhb_detail …），本轮新增的「可操作闭环」
需要可写表。全部用 ``CREATE TABLE IF NOT EXISTS`` 幂等建表，启动时执行一次，
失败不阻断启动（远端库没权限时接口会显式报错，而不是让服务起不来）。
"""
from __future__ import annotations

import threading

from .db import execute

_LOCK = threading.Lock()
_DONE = False

_DDL = [
    # ---------- 自选 ----------
    """
    CREATE TABLE IF NOT EXISTS app_watchlist_groups (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id     uuid,
        name        varchar(64)  NOT NULL,
        sort        int          NOT NULL DEFAULT 0,
        is_shared   boolean      NOT NULL DEFAULT false,
        created_at  timestamptz  NOT NULL DEFAULT now(),
        updated_at  timestamptz  NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_watchlist_items (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        group_id    uuid         NOT NULL,
        ts_code     varchar(20)  NOT NULL,
        stock_name  varchar(64),
        sort        int          NOT NULL DEFAULT 0,
        note        text,
        created_at  timestamptz  NOT NULL DEFAULT now(),
        CONSTRAINT uq_wl_item UNIQUE (group_id, ts_code)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_wl_items_group ON app_watchlist_items (group_id, sort)
    """,
    # ---------- 持仓 ----------
    """
    CREATE TABLE IF NOT EXISTS app_holdings (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id     uuid,
        ts_code     varchar(20)  NOT NULL,
        stock_name  varchar(64),
        qty         int          NOT NULL DEFAULT 0,
        cost        numeric(14,4) NOT NULL DEFAULT 0,
        path        varchar(32),
        entry_date  date,
        notes       text,
        created_at  timestamptz  NOT NULL DEFAULT now(),
        updated_at  timestamptz  NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_holdings_code ON app_holdings (ts_code)
    """,
    # ---------- 提醒 / 异动规则 ----------
    """
    CREATE TABLE IF NOT EXISTS app_alerts (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id           uuid,
        ts_code           varchar(20),
        stock_name        varchar(64),
        alert_type        varchar(32)  NOT NULL,
        scope             varchar(16)  NOT NULL DEFAULT 'stock',
        threshold         numeric(14,4),
        direction         varchar(8),
        enabled           boolean      NOT NULL DEFAULT true,
        trigger_count     int          NOT NULL DEFAULT 0,
        last_triggered_at timestamptz,
        last_message      text,
        created_at        timestamptz  NOT NULL DEFAULT now(),
        updated_at        timestamptz  NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_anomaly_rules (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id     uuid,
        name        varchar(80)  NOT NULL,
        scope       varchar(24)  NOT NULL DEFAULT 'symbol',
        kind        varchar(32)  NOT NULL,
        params      jsonb        NOT NULL DEFAULT '{}'::jsonb,
        enabled     boolean      NOT NULL DEFAULT true,
        run_count   int          NOT NULL DEFAULT 0,
        last_run_at timestamptz,
        created_at  timestamptz  NOT NULL DEFAULT now(),
        updated_at  timestamptz  NOT NULL DEFAULT now()
    )
    """,
    # ---------- 模拟盘 ----------
    """
    CREATE TABLE IF NOT EXISTS app_sim_accounts (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id      uuid,
        name         varchar(64)   NOT NULL DEFAULT '默认账户',
        initial_cash numeric(18,2) NOT NULL DEFAULT 1000000,
        cash         numeric(18,2) NOT NULL DEFAULT 1000000,
        created_at   timestamptz   NOT NULL DEFAULT now(),
        updated_at   timestamptz   NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_sim_orders (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id   uuid         NOT NULL,
        ts_code      varchar(20)  NOT NULL,
        stock_name   varchar(64),
        side         varchar(8)   NOT NULL,
        order_type   varchar(12)  NOT NULL DEFAULT 'market',
        price        numeric(14,4),
        qty          int          NOT NULL,
        status       varchar(12)  NOT NULL DEFAULT 'filled',
        filled_price numeric(14,4),
        filled_qty   int          NOT NULL DEFAULT 0,
        fee          numeric(14,4) NOT NULL DEFAULT 0,
        reason       text,
        filled_at    timestamptz,
        created_at   timestamptz  NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sim_orders_acct ON app_sim_orders (account_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS app_sim_positions (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id    uuid         NOT NULL,
        ts_code       varchar(20)  NOT NULL,
        stock_name    varchar(64),
        qty           int          NOT NULL DEFAULT 0,
        cost          numeric(14,4) NOT NULL DEFAULT 0,
        realized_pnl  numeric(18,2) NOT NULL DEFAULT 0,
        fee_total     numeric(18,2) NOT NULL DEFAULT 0,
        updated_at    timestamptz  NOT NULL DEFAULT now(),
        CONSTRAINT uq_sim_pos UNIQUE (account_id, ts_code)
    )
    """,
    # ---------- 反馈 ----------
    """
    CREATE TABLE IF NOT EXISTS app_feedbacks (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id     uuid,
        category    varchar(32)  NOT NULL DEFAULT 'bug',
        content     text         NOT NULL,
        contact     varchar(160),
        status      varchar(16)  NOT NULL DEFAULT 'open',
        admin_reply text,
        replied_at  timestamptz,
        created_at  timestamptz  NOT NULL DEFAULT now()
    )
    """,
    # ---------- 策略 ----------
    """
    CREATE TABLE IF NOT EXISTS app_strategy_defs (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        code        varchar(64)  NOT NULL UNIQUE,
        name        varchar(80)  NOT NULL,
        family      varchar(32)  NOT NULL DEFAULT 'trend',
        description text,
        params      jsonb        NOT NULL DEFAULT '{}'::jsonb,
        enabled     boolean      NOT NULL DEFAULT true,
        sort        int          NOT NULL DEFAULT 0,
        created_at  timestamptz  NOT NULL DEFAULT now(),
        updated_at  timestamptz  NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_strategy_runs (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        strategy_code varchar(64)  NOT NULL,
        run_date      date         NOT NULL,
        status        varchar(16)  NOT NULL DEFAULT 'ok',
        total_scanned int          NOT NULL DEFAULT 0,
        total_passed  int          NOT NULL DEFAULT 0,
        elapsed_ms    int          NOT NULL DEFAULT 0,
        hits          jsonb        NOT NULL DEFAULT '[]'::jsonb,
        stats         jsonb        NOT NULL DEFAULT '{}'::jsonb,
        created_at    timestamptz  NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_strat_runs ON app_strategy_runs (strategy_code, run_date DESC)
    """,
    # ---------- 复盘 / 情绪历史 ----------
    """
    CREATE TABLE IF NOT EXISTS app_reviews (
        trade_date   date NOT NULL,
        region       varchar(8) NOT NULL DEFAULT 'cn',
        content      jsonb NOT NULL,
        generated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (trade_date, region)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_sentiment_history (
        trade_date date PRIMARY KEY,
        score      numeric(6,2),
        mood       varchar(16),
        metrics    jsonb NOT NULL DEFAULT '{}'::jsonb,
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
]


def init_schema(force: bool = False) -> dict:
    """幂等建表；进程内只跑一次（除非 force）。"""
    global _DONE
    if _DONE and not force:
        return {"ok": True, "skipped": True}
    with _LOCK:
        if _DONE and not force:
            return {"ok": True, "skipped": True}
        created, errors = [], []
        for stmt in _DDL:
            try:
                execute(" ".join(stmt.split()))
                created.append(1)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc)[:200])
        _DONE = True
    return {"ok": not errors, "tables": len(_DDL), "errors": errors}
