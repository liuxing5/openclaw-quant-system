"""回调买入法 — 信号引擎

在 daily_quotes 日线上实现推文所述 Martin Luk 回调买入法的四个步骤，
供 run_backtest.py（回测）与 run_daily.py（每日选股）复用。

四步骤（信号日 T）:
  1. 强势股筛选   : 120日动能位于当日截面前30%且为正，EMA9>EMA21>EMA50 多头排列，收盘价>EMA50
  2. 回调到支撑   : 近5日最大回撤自20日高点 5%~25%，低点触及 EMA21/EMA50 支撑区，
                    且回调段缩量（3日均量≤90%×20日均量）
  3. 价格回升确认 : T 日收阳、收盘高于前一日收盘、收盘重新站上 EMA21
  4. 波动率止损   : 止损=min(结构止损, close-1.5×ATR14)，风险上限6%，目标=入场+2×风险

2026-10-13 参数审查修复:
  - 动量: 20日→120日形成期 + 截面百分位（A股<1周动量有效、更长反转；绝对阈值随 regime 漂移）
  - 止损: 固定3%比日噪声窄（回测67%被噪声止损）→ 1.5×ATR14 波动率下限
  - 涨停: pct_chg<9.9 近似 → 按板块规则计算真实涨停价（20cm/30cm 票原先全部误判）
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from core.db.connection import get_db_fresh
from strategies.shared.board_rules import limit_pct
from .config import PullbackConfig, DEFAULT_CONFIG

NUM_COLS = ["open", "high", "low", "close", "volume", "amount", "pct_chg", "limit_up_price"]


def _close(conn):
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _fetch(sql, params=None, retries: int = 3, timeout_ms: int = 600000):
    """执行查询并返回 (rows, cols)，SSL 瞬断时重试。

    2026-10-14: 连接默认 statement_timeout=60s，全市场日线（预热 260 天 +
    回测区间）一次拉取会超时，故会话级提升到 timeout_ms（默认 10 分钟）。
    """
    import time
    last = None
    for attempt in range(retries):
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor()
            if timeout_ms:
                cur.execute("SET statement_timeout = %s", (int(timeout_ms),))
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


def _fetch_streaming(sql, params=None, timeout_ms: int = 900000, chunk: int = 50000):
    """服务端游标流式拉取，返回 (rows, cols)。

    2026-10-14: 单次 fetchall 拉取 70 万+行时 SSL 连接会被对端直接断开
    （小查询正常），改用命名游标分批读取。已实测 72.7 万行可完整取回。
    注意：命名游标的 description 为 None（DECLARE 语句本身无结果集），
    列名需用普通游标单独取。
    """
    conn = get_db_fresh()
    try:
        # statement_timeout 必须在普通游标上设置：命名游标会把语句包进
        # DECLARE ... CURSOR FOR <sql>，直接 execute("SET ...") 会语法报错
        if timeout_ms:
            with conn.cursor() as pre:
                pre.execute("SET statement_timeout = %s", (int(timeout_ms),))
        cols = None
        with conn.cursor() as meta:
            meta.execute(f"SELECT * FROM ({sql.rstrip().rstrip(';')}) AS _q LIMIT 0", params)
            cols = [d[0] for d in meta.description]
        cur = conn.cursor(name="stream_fetch")
        cur.itersize = chunk
        cur.execute(sql, params)
        rows = []
        while True:
            batch = cur.fetchmany(chunk)
            if not batch:
                break
            rows.extend(batch)
        cur.close()
        return rows, cols
    finally:
        _close(conn)


def load_quotes(start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """拉取日线（含指标预热期），返回 float 化的 DataFrame。

    2026-10-14: 跨度 >= 60 天时（数十万行）fetchall 会触发 SSL 断连，
    改走服务端游标流式拉取；短跨度仍用普通查询（更快）。
    """
    sql = """
        SELECT ts_code, trade_date, open, high, low, close, volume, amount,
               pct_chg, limit_up_price
        FROM daily_quotes
        WHERE trade_date >= %s AND (%s::date IS NULL OR trade_date <= %s)
        ORDER BY ts_code, trade_date;
    """
    span_days = 60
    try:
        if end_date:
            span_days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
    except Exception:
        span_days = 60
    if span_days >= 60:
        rows, cols = _fetch_streaming(sql, (start_date, end_date, end_date))
    else:
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
    # 中期动量形成期（2026-10-13 审查: 20日处于A股反转区间 → 120日=6个月中期动量）
    df["mom"] = g.pct_change(cfg.momentum_lookback)
    # 当日截面动能百分位（替代绝对阈值，随 regime 归一）
    df["mom_pctile"] = df.groupby("trade_date")["mom"].rank(pct=True)
    # ATR14（波动率止损宽度，问题6: 3% 固定止损比日噪声还窄）
    _tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["prevc"]).abs(),
        (df["low"] - df["prevc"]).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = _tr.groupby(df["ts_code"], sort=False).transform(
        lambda s: s.ewm(alpha=1 / 14, adjust=False).mean()
    )
    return df


def not_limit_up(d: pd.DataFrame) -> pd.Series:
    """当日收盘未涨停（可买入）。

    2026-10-13 审查修复（问题7）: 原 `pct_chg < 9.9` 对创业板/科创板（20cm）
    与北交所（30cm）全部误判。现按板块规则用前收盘计算真实涨停价；
    仅当 limit_up_price 列有值时优先用库内值。
    """
    prev = d["prevc"]
    pct = d["ts_code"].map(limit_pct)
    lu_calc = (prev.astype("float64") * (1.0 + pct)).round(2)
    lu_db = d.get("limit_up_price")
    if lu_db is not None:
        ok = lu_db.notna() & (lu_db > 0)
        lu = pd.Series(np.where(ok, lu_db.astype("float64"), lu_calc), index=d.index)
    else:
        lu = lu_calc
    # 前收盘缺失（首根K线）无法判断 → 视为未涨停（次新股已被剔除）
    not_lu = (d["close"].astype("float64") < lu - 0.001) | prev.isna()
    return not_lu


def not_limit_down(d: pd.DataFrame) -> pd.Series:
    """当日收盘未跌停（跌停次日常继续跌，且卖出困难）——板块感知。"""
    prev = d["prevc"]
    pct = d["ts_code"].map(limit_pct)
    ld = (prev.astype("float64") * (1.0 - pct)).round(2)
    return (d["close"].astype("float64") > ld + 0.001) | prev.isna()


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
    # 2026-10-13: 动量改 120 日形成期 + 当日截面百分位（原 20 日≥20% 绝对阈值：
    # ①20日在A股是反转区间 ②绝对阈值随 regime 漂移）
    s1 = (
        (d["mom_pctile"] >= cfg.min_momentum_pctile / 100.0)
        & (d["mom"] > cfg.min_momentum_pct / 100.0)
        & (d["ema9"] > d["ema21"])
        & (d["ema21"] > d["ema50"])
        & (d["close"] > d["ema50"])
        & d["mom"].notna()
        & d["mom_pctile"].notna()
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
        & not_limit_up(d)                              # 当日未涨停（可成交，板块感知）
        & not_limit_down(d)                            # 当日未跌停（原 pct_chg>-9.5 近似已替换）
    )

    sig = d[s1 & s2 & s3 & base].copy()

    # ---- 步骤4: ATR 波动率止损（2026-10-13 审查问题6）----
    # 原实现: 止损=min(低点,EMA21,EMA50)-1% 且硬压到风险≤3%，比 A 股日噪声还窄，
    #         回测 67% 以止损离场（平均持有仅 2.65 日）。
    # 新实现: 结构止损与 1.5×ATR14 取更宽者（宁可多担波动，不被噪声打掉），
    #         但风险上限放宽到 max_risk_pct（6%）作最后保护；不足 ATR 宽度则放宽。
    atr_floor = sig["close"].astype("float64") - cfg.atr_stop_mult * sig["atr14"].astype("float64")
    stop_struct = np.minimum(np.minimum(sig["low5"], sig["ema21"]), sig["ema50"]) * (
        1 - cfg.stop_below_support_pct / 100.0
    )
    stop_cap = sig["close"].astype("float64") * (1 - cfg.max_risk_pct / 100.0)
    # 取结构止损与 ATR 下限中更低（更宽）者，再用 stop_cap 兜底不越上限
    sig["stop_loss"] = np.maximum(np.minimum(stop_struct, atr_floor), stop_cap)
    sig["risk_pct"] = (sig["close"] - sig["stop_loss"]) / sig["close"]
    sig["target_1"] = sig["close"] + cfg.reward_risk * (sig["close"] - sig["stop_loss"])
    sig = sig[sig["atr14"].notna()]
    sig = sig[(sig["risk_pct"] > 0) & (sig["risk_pct"] <= cfg.max_risk_pct / 100.0 + 1e-6)]

    # ---- 综合评分（用于排序，满分100）----
    mom_pts = np.clip(sig["mom_pctile"], 0, 1) * 50                    # 截面动能分位
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
        & not_limit_up(m) & not_limit_down(m)
    )
    n_base = int(base.sum())
    s1 = base & (m["mom_pctile"] >= cfg.min_momentum_pctile / 100.0) \
        & (m["mom"] > cfg.min_momentum_pct / 100.0) & (m["ema9"] > m["ema21"]) \
        & (m["ema21"] > m["ema50"]) & (m["close"] > m["ema50"]) \
        & m["mom"].notna() & m["mom_pctile"].notna()
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
    # 步骤4: ATR 波动率止损（与 generate_signals 相同口径）
    sub = m[s3]
    if len(sub):
        atr_floor = sub["close"].astype("float64") - cfg.atr_stop_mult * sub["atr14"].astype("float64")
        stop_struct = np.minimum(np.minimum(sub["low5"], sub["ema21"]), sub["ema50"]) * (
            1 - cfg.stop_below_support_pct / 100.0)
        stop_cap = sub["close"].astype("float64") * (1 - cfg.max_risk_pct / 100.0)
        stop = np.maximum(np.minimum(stop_struct, atr_floor), stop_cap)
        risk = (sub["close"] - stop) / sub["close"]
        n4 = int((sub["atr14"].notna() & (risk > 0)
                  & (risk <= cfg.max_risk_pct / 100.0 + 1e-6)).sum())
    else:
        n4 = 0
    return {
        "流动性/风控": n_base,
        "强势股筛选": n1,
        "回调至支撑": n2,
        "回升确认": n3,
        "波动率止损达标": n4,
    }
