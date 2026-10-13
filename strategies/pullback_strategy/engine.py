"""回调买入法 — 信号引擎

在 daily_quotes 日线上实现推文所述 Martin Luk 回调买入法的四个步骤，
供 run_backtest.py（回测）与 run_daily.py（每日选股）复用。

四步骤（信号日 T）:
  1. 强势股筛选   : 20日动能≥20%，EMA9>EMA21>EMA50 多头排列，收盘价>EMA50
  2. 回调到支撑   : 近5日最大回撤自20日高点 5%~30%，低点触及 EMA21/EMA50 支撑区，
                    且回调段缩量（3日均量≤90%×20日均量）
  3. 价格回升确认 : T 日收阳、收盘高于前一日收盘、收盘重新站上 EMA21
  4. 紧止损入场   : 止损=min(近5日低点, EMA21, EMA50)下方1%，风险≤3%（超出则收紧到3%），
                    目标=入场+2×风险
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from core.db.connection import get_db_fresh
from .config import PullbackConfig, DEFAULT_CONFIG

NUM_COLS = ["open", "high", "low", "close", "volume", "amount", "pct_chg", "limit_up_price"]


def _close(conn):
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _fetch(sql, params=None, retries: int = 3):
    """执行查询并返回 (rows, cols)，SSL 瞬断时重试。"""
    import time
    last = None
    for attempt in range(retries):
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            cur.close()
            return rows, cols
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
        finally:
            _close(conn)
    raise last


def load_quotes(start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """拉取日线（含指标预热期），返回 float 化的 DataFrame。"""
    sql = """
        SELECT ts_code, trade_date, open, high, low, close, volume, amount,
               pct_chg, limit_up_price
        FROM daily_quotes
        WHERE trade_date >= %s AND (%s::date IS NULL OR trade_date <= %s)
        ORDER BY ts_code, trade_date;
    """
    rows, cols = _fetch(sql, (start_date, end_date, end_date))
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for c in NUM_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    return df


def load_stock_meta() -> pd.DataFrame:
    rows, cols = _fetch("SELECT ts_code, stock_name, is_st, list_date FROM stock_basic_info;")
    df = pd.DataFrame(rows, columns=cols)
    df["is_st"] = df["is_st"].fillna(False).astype(bool)
    df["list_date"] = pd.to_datetime(df["list_date"])
    return df


def add_indicators(df: pd.DataFrame, cfg: PullbackConfig) -> pd.DataFrame:
    g = df.groupby("ts_code", sort=False)["close"]
    lo = df.groupby("ts_code", sort=False)["low"]
    hi = df.groupby("ts_code", sort=False)["high"]
    vol = df.groupby("ts_code", sort=False)["volume"]

    df["ema9"] = g.transform(lambda s: s.ewm(span=cfg.ema_fast, adjust=False).mean())
    df["ema21"] = g.transform(lambda s: s.ewm(span=cfg.ema_mid, adjust=False).mean())
    df["ema50"] = g.transform(lambda s: s.ewm(span=cfg.ema_slow, adjust=False).mean())
    # 20日高点（不含当日，用 shift(1) 保证"自前期高点回撤"语义）
    df["hi20_prev"] = hi.transform(
        lambda s: s.rolling(cfg.pullback_lookback, min_periods=cfg.pullback_lookback).max().shift(1)
    )
    # 近5日最低点（含当日）
    df["low5"] = lo.transform(lambda s: s.rolling(cfg.stop_lookback, min_periods=1).min())
    df["prevc"] = g.shift(1)
    df["vol20_prev"] = vol.transform(
        lambda s: s.rolling(20, min_periods=20).mean().shift(1)
    )
    df["vol3_prev"] = vol.transform(
        lambda s: s.rolling(3, min_periods=3).mean().shift(1)
    )
    df["mom20"] = g.pct_change(cfg.momentum_lookback)
    return df


def not_limit_up(d: pd.DataFrame) -> pd.Series:
    """当日收盘未涨停（可买入）。

    `limit_up_price` 仅 2026-05 起有数据，历史缺失时用 pct_chg 近似
    （涨停 ≈ +10%，留 0.1% 容差），避免历史日期被整体误杀。
    """
    lu = d.get("limit_up_price")
    if lu is None:
        return pd.Series(True, index=d.index)
    missing = lu.isna() | (lu <= 0)
    approx_ok = d["pct_chg"] < 9.9
    return (~missing & (d["close"] < lu * 0.999)) | (missing & approx_ok)


def generate_signals(df: pd.DataFrame, cfg: PullbackConfig, meta: pd.DataFrame,
                     start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """返回信号日明细（含 entry/stop/target 与各步骤通过计数所需的列）。"""
    df = df.merge(meta, on="ts_code", how="left")
    df["stock_name"] = df["stock_name"].fillna("")
    df["is_st"] = df["is_st"].fillna(False)
    df["list_date"] = pd.to_datetime(df["list_date"])

    d = df
    if start:
        d = d[d["trade_date"] >= pd.Timestamp(start)]
    if end:
        d = d[d["trade_date"] <= pd.Timestamp(end)]

    # ---- 步骤1: 强势股 ----
    s1 = (
        (d["mom20"] >= cfg.min_momentum_pct / 100.0)
        & (d["ema9"] > d["ema21"])
        & (d["ema21"] > d["ema50"])
        & (d["close"] > d["ema50"])
        & d["mom20"].notna()
    )

    # ---- 步骤2: 回调到支撑 ----
    depth = 1.0 - d["low5"] / d["hi20_prev"]          # 自20日高点的最大回撤深度
    touched = (d["low5"] <= d["ema21"] * (1 + cfg.support_tolerance_pct / 100.0)) | (
        d["low5"] <= d["ema50"] * (1 + cfg.support_tolerance_pct / 100.0)
    )
    shrunk = d["vol3_prev"] <= d["vol20_prev"] * cfg.volume_shrink_ratio
    s2 = (
        (depth >= cfg.min_pullback_pct / 100.0)
        & (depth <= cfg.max_pullback_pct / 100.0)
        & touched
        & shrunk
        & d["hi20_prev"].notna()
    )

    # ---- 步骤3: 价格回升确认（三项条件需同时满足）----
    s3 = pd.Series(True, index=d.index)
    if cfg.require_bullish_day:
        s3 &= (d["close"] > d["open"])
    if cfg.require_beat_prev_close:
        s3 &= (d["close"] > d["prevc"])
    if cfg.require_reclaim_ema21:
        s3 &= (d["close"] >= d["ema21"])

    # ---- 基础过滤 ----
    age_ok = (d["trade_date"] - d["list_date"]).dt.days >= cfg.exclude_new_days
    base = (
        (d["amount"] >= cfg.min_amount_yi * 1e8)
        & (d["close"] >= cfg.min_close)
        & (~d["is_st"].astype(bool))
        & age_ok
        & not_limit_up(d)                              # 当日未涨停（可成交）
        & (d["pct_chg"] > -9.5)
    )

    sig = d[s1 & s2 & s3 & base].copy()

    # ---- 步骤4: 紧止损/目标 ----
    stop_raw = np.minimum(np.minimum(sig["low5"], sig["ema21"]), sig["ema50"]) * (
        1 - cfg.stop_below_support_pct / 100.0
    )
    stop_tight = sig["close"] * (1 - cfg.max_risk_pct / 100.0)
    sig["stop_loss"] = np.maximum(stop_raw, stop_tight)     # 风险超3%则收紧
    sig["risk_pct"] = (sig["close"] - sig["stop_loss"]) / sig["close"]
    sig["target_1"] = sig["close"] + cfg.reward_risk * (sig["close"] - sig["stop_loss"])
    sig = sig[(sig["risk_pct"] > 0) & (sig["risk_pct"] <= cfg.max_risk_pct / 100.0 + 1e-6)]

    # ---- 综合评分（用于排序，满分100）----
    mom_pts = np.clip(sig["mom20"] / 0.5, 0, 1) * 50                 # 动能越强越高
    depth_pts = (1 - np.abs(depth.reindex(sig.index) - 0.15) / 0.15).clip(0, 1) * 25
    risk_pts = (1 - sig["risk_pct"] / (cfg.max_risk_pct / 100.0)) * 25
    sig["final_score"] = (mom_pts + depth_pts + risk_pts).round(1)
    sig["quant_score"] = sig["final_score"]
    sig["step1_pass"] = 1
    return sig.reset_index(drop=True)


def step_counts(df: pd.DataFrame, cfg: PullbackConfig, meta: pd.DataFrame,
                date) -> dict:
    """统计某一日各步骤累计通过数量（供 HTML 漏斗展示）。"""
    d = df[df["trade_date"] == pd.Timestamp(date)]
    if d.empty:
        return {}
    m = d.merge(meta, on="ts_code", how="left")
    m["is_st"] = m["is_st"].fillna(False)
    age_ok = (m["trade_date"] - pd.to_datetime(m["list_date"])).dt.days >= cfg.exclude_new_days
    base = (
        (m["amount"] >= cfg.min_amount_yi * 1e8) & (m["close"] >= cfg.min_close)
        & (~m["is_st"].astype(bool)) & age_ok
        & not_limit_up(m) & (m["pct_chg"] > -9.5)
    )
    n_base = int(base.sum())
    s1 = base & (m["mom20"] >= cfg.min_momentum_pct / 100.0) & (m["ema9"] > m["ema21"]) \
        & (m["ema21"] > m["ema50"]) & (m["close"] > m["ema50"]) & m["mom20"].notna()
    n1 = int(s1.sum())
    depth = 1.0 - m["low5"] / m["hi20_prev"]
    touched = (m["low5"] <= m["ema21"] * (1 + cfg.support_tolerance_pct / 100.0)) | (
        m["low5"] <= m["ema50"] * (1 + cfg.support_tolerance_pct / 100.0))
    shrunk = m["vol3_prev"] <= m["vol20_prev"] * cfg.volume_shrink_ratio
    s2 = s1 & (depth >= cfg.min_pullback_pct / 100.0) & (depth <= cfg.max_pullback_pct / 100.0) \
        & touched & shrunk & m["hi20_prev"].notna()
    n2 = int(s2.sum())
    s3 = s2 & (m["close"] > m["open"]) & (m["close"] > m["prevc"]) & (m["close"] >= m["ema21"])
    n3 = int(s3.sum())
    # 步骤4: 止损几何约束（与 generate_signals 相同口径）
    sub = m[s3]
    if len(sub):
        stop_raw = np.minimum(np.minimum(sub["low5"], sub["ema21"]), sub["ema50"]) * (
            1 - cfg.stop_below_support_pct / 100.0)
        stop_tight = sub["close"] * (1 - cfg.max_risk_pct / 100.0)
        stop = np.maximum(stop_raw, stop_tight)
        risk = (sub["close"] - stop) / sub["close"]
        n4 = int(((risk > 0) & (risk <= cfg.max_risk_pct / 100.0 + 1e-6)).sum())
    else:
        n4 = 0
    return {
        "流动性/风控": n_base,
        "强势股筛选": n1,
        "回调至支撑": n2,
        "回升确认": n3,
        "紧止损达标": n4,
    }
