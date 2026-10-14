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

import logging
import time
from datetime import timedelta

import numpy as np
import pandas as pd

from core.db.connection import get_db_fresh
from strategies.shared.adjust import qfq_grouped
from strategies.shared.board_rules import limit_pct
from strategies.shared.st_history import is_st_mask
from .config import PullbackConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)

# 2026-10-14 审查修复（问题20）: OHLC 用 float64——float32 在涨停价/止损临界
# （0.01 元量级）比较时会因精度不足误判；量额等非价格列保持 float32 省内存
PRICE_COLS = ["open", "high", "low", "close"]
NUM_COLS = PRICE_COLS + ["volume", "amount", "pct_chg", "limit_up_price"]


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


def _fetch_streaming(sql, params=None, timeout_ms: int = 900000, chunk: int = 50000,
                     retries: int = 3):
    """服务端游标流式拉取，返回 (rows, cols)。

    2026-10-14: 单次 fetchall 拉取 70 万+行时 SSL 连接会被对端直接断开
    （小查询正常），改用命名游标分批读取。已实测 72.7 万行可完整取回。
    注意：命名游标的 description 为 None（DECLARE 语句本身无结果集），
    列名需用普通游标单独取。
    2026-10-14 审查修复（问题16）: 原实现无重试，流式拉取中途 SSL 断连即整跑失败，
    现包 3 次重试（每次重建连接，命名游标不可跨连接复用）。
    """
    last = None
    for attempt in range(retries):
        conn = None
        try:
            conn = get_db_fresh()
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
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
        finally:
            _close(conn)
    raise last


def latest_trade_date() -> str | None:
    """daily_quotes 最新交易日（SELECT MAX 单查询，2026-10-14 审查修复问题14）。"""
    rows, _ = _fetch("SELECT MAX(trade_date) FROM daily_quotes;")
    d = rows[0][0] if rows else None
    return str(d)[:10] if d is not None else None


def load_quotes(start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """拉取日线（含指标预热期），返回前复权后的 DataFrame。

    2026-10-14: 跨度 >= 60 天时（数十万行）fetchall 会触发 SSL 断连，
    改走服务端游标流式拉取；短跨度仍用普通查询（更快）。
    2026-10-14 审查修复（问题4）: 取数后按 ts_code 前复权（qfq_grouped），
    指标（EMA/ATR/动量/止损止盈/前向收益）全部在复权价上计算；
    保留 raw_close/raw_open 列——涨停价计算与除权日检测必须用原始价。
    末日锚定复权：最新一日 qfq == raw，run_daily 展示价格不受影响。
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
    for c in PRICE_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in NUM_COLS:
        if c not in PRICE_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    # 问题4: 前复权（保留 raw 列）；组内已按 trade_date 升序
    df = qfq_grouped(df)
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
    # 2026-10-14 审查修复（问题4/15）: 原始价前收盘——涨停/跌停判定与除权检测
    # 必须用 raw 价（复权价与板块规则算出的涨停价不可比）
    if "raw_close" in df.columns:
        df["raw_prevc"] = df.groupby("ts_code", sort=False)["raw_close"].shift(1)
    else:  # 兜底：未复权输入时 raw == close
        df["raw_prevc"] = df["prevc"]
        df["raw_close"] = df["close"]
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
    与北交所（30cm）全部误判。现按板块规则用前收盘计算真实涨停价。
    2026-10-14 审查修复（问题15）: 翻转优先级——以 prevc+板块规则计算值为准
    （库内 limit_up_price 列历史几乎全空且口径不明），库内值仅作一致性校验，
    分歧 >0.02 元时记 warning 一次。判定用 raw 价（问题4：复权价与板块
    规则算出的涨停价不可比）。
    """
    prev = d["raw_prevc"] if "raw_prevc" in d.columns else d["prevc"]
    raw_close = d["raw_close"] if "raw_close" in d.columns else d["close"]
    pct = d["ts_code"].map(limit_pct)
    lu_calc = (prev.astype("float64") * (1.0 + pct)).round(2)
    lu = lu_calc
    lu_db = d.get("limit_up_price")
    if lu_db is not None:
        # 库内值仅校验：与板块规则计算值分歧过大时告警（不改判定结果）
        ok = lu_db.notna() & (lu_db > 0) & prev.notna()
        mismatch = ok & ((lu_db.astype("float64") - lu_calc).abs() > 0.02)
        if bool(mismatch.any()):
            logger.warning(
                "limit_up_price 列与板块规则计算值分歧 %d 行（以板块规则为准）",
                int(mismatch.sum()))
    # 前收盘缺失（首根K线）无法判断 → 视为未涨停（次新股已被剔除）
    not_lu = (raw_close.astype("float64") < lu - 0.001) | prev.isna()
    return not_lu


def not_limit_down(d: pd.DataFrame) -> pd.Series:
    """当日收盘未跌停（跌停次日常继续跌，且卖出困难）——板块感知。

    2026-10-14: 同 not_limit_up，改用 raw 价判定（问题4）。
    """
    prev = d["raw_prevc"] if "raw_prevc" in d.columns else d["prevc"]
    raw_close = d["raw_close"] if "raw_close" in d.columns else d["close"]
    pct = d["ts_code"].map(limit_pct)
    ld = (prev.astype("float64") * (1.0 - pct)).round(2)
    return (raw_close.astype("float64") > ld + 0.001) | prev.isna()


def limit_down_mask(g: pd.DataFrame) -> np.ndarray:
    """逐日跌停判定（用于回测出场建模，2026-10-14 审查修复问题13）。

    g: 单只股票按日升序 DataFrame（含 raw_close/raw_prevc）。
    返回 bool 数组：当日 raw 收盘 <= 板块规则跌停价（容差 1 分）。
    """
    prev = g["raw_prevc"] if "raw_prevc" in g.columns else g["prevc"]
    raw_close = g["raw_close"] if "raw_close" in g.columns else g["close"]
    pct = g["ts_code"].map(limit_pct)
    ld = (prev.astype("float64") * (1.0 - pct)).round(2)
    return ((raw_close.astype("float64") <= ld + 0.001)
            & prev.notna()).to_numpy()


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
    # 2026-10-14 审查修复（问题10）: list_date 缺失（NaT）原会静默永久剔除，
    # 现放行并计数告警（次新误剔与 ST 快照误剔都属数据缺失，不能无声吃掉）
    age_missing = d["list_date"].isna()
    if bool(age_missing.any()):
        logger.warning("list_date 缺失 %d 行（不按次新剔除，放行）",
                       int(age_missing.sum()))
    age_ok = age_missing | (
        (d["trade_date"] - d["list_date"]).dt.days >= cfg.exclude_new_days)
    base = (
        (d["amount"] >= cfg.min_amount_yi * 1e8)
        & (d["close"] >= cfg.min_close)
        & age_ok
        & not_limit_up(d)                              # 当日未涨停（可成交，板块感知）
        & not_limit_down(d)                            # 当日未跌停（原 pct_chg>-9.5 近似已替换）
    )

    # 2026-10-14 审查修复（问题3）: ST 改按信号日 PIT 判定（stock_st_history），
    # 原 is_st 来自当前快照回溯历史属未来函数。先粗筛再按日查，控制查询量。
    cand = d[s1 & s2 & s3 & base]
    st_mask = pd.Series(False, index=cand.index)
    if cfg.exclude_st and not cand.empty:
        conn = get_db_fresh()
        try:
            for dt, idx in cand.groupby(cand["trade_date"].dt.date).groups.items():
                codes = cand.loc[idx, "ts_code"].tolist()
                st_mask.loc[idx] = is_st_mask(conn, codes, dt)
        finally:
            _close(conn)
    sig = cand[~st_mask].copy()

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
    m["stock_name"] = m["stock_name"].fillna("")
    m["list_date"] = pd.to_datetime(m["list_date"])
    # 问题10: list_date 缺失放行；问题3: ST 按当日 PIT 判定（与 generate_signals 一致）
    age_missing = m["list_date"].isna()
    age_ok = age_missing | (
        (m["trade_date"] - pd.to_datetime(m["list_date"])).dt.days >= cfg.exclude_new_days)
    st_mask = np.zeros(len(m), dtype=bool)
    if cfg.exclude_st and len(m):
        conn = get_db_fresh()
        try:
            st_mask = is_st_mask(conn, m["ts_code"].tolist(), pd.Timestamp(date).date())
        finally:
            _close(conn)
    base = (
        (m["amount"] >= cfg.min_amount_yi * 1e8) & (m["close"] >= cfg.min_close)
        & (~pd.Series(st_mask, index=m.index).astype(bool)) & age_ok
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
