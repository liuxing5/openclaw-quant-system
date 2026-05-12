"""
Layer 3: 趋势结构过滤
======================
决策逻辑：
  周线CLOSE>20MA；日线EMA12>26>50，且股价在EMA12上方；
  股价>200EMA可加分；结构为上升平台或回踩支撑。

吸收策略：②20周保命法/均线多头/年线定海神针/右侧交易
"""
from __future__ import annotations

import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import List, Dict, Optional

import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    tqdm = None

LAYER3_WORKERS = min(8, (os.cpu_count() or 4))


def _calc_ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False).mean()


def _batch_load_history(
    stock_list: List[str], trade_date: date, db_conn, days: int = 300
) -> Dict[str, pd.DataFrame]:
    """批量加载所有股票的OHLCV历史数据（1次SQL查询）"""
    if not stock_list:
        return {}

    start_date = trade_date - timedelta(days=days)
    result = {}

    cur = db_conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT ts_code, trade_date, open, high, low, close, volume
        FROM daily_quotes
        WHERE ts_code = ANY(%s) AND trade_date >= %s AND trade_date <= %s
        ORDER BY ts_code, trade_date ASC;
    """, (stock_list, start_date, trade_date))
    rows = cur.fetchall()
    cur.close()

    if not rows:
        return result

    df_all = pd.DataFrame(rows)
    df_all['trade_date'] = pd.to_datetime(df_all['trade_date']).dt.date

    for ts_code, group in df_all.groupby('ts_code'):
        group = group.set_index('trade_date').sort_index()
        for col in ['open', 'high', 'low', 'close', 'volume']:
            group[col] = pd.to_numeric(group[col], errors='coerce')
        result[ts_code] = group

    return result


def _detect_trend_structure(df: pd.DataFrame, cfg) -> dict:
    """识别趋势结构：上升平台 / 回踩支撑 / 未知"""
    if len(df) < 30:
        return {'structure': 'unknown'}

    close = df['close']
    ema12 = _calc_ema(close, 12)

    # 回踩支撑：近3日最低价一度贴近EMA12（±2%），然后收回
    if len(df) >= 5:
        recent_low_5 = df['low'].iloc[-5:].min()
        ema12_latest = ema12.iloc[-1]
        if abs(recent_low_5 - ema12_latest) / ema12_latest <= 0.03:
            if close.iloc[-1] > ema12_latest:
                return {'structure': 'pullback_support'}

    # 上升平台：近10日高低点区间收窄（振幅<5%），今日突破上沿
    if len(df) >= 10:
        hi_10 = df['high'].iloc[-10:].max()
        lo_10 = df['low'].iloc[-10:].min()
        range_pct = (hi_10 - lo_10) / lo_10 if lo_10 > 0 else 1
        if range_pct < 0.08 and close.iloc[-1] >= hi_10 * 0.99:
            return {'structure': 'ascending_platform'}

    return {'structure': 'unknown'}


def _check_single(
    ts_code: str, cfg, ohlcv_cache: Dict[str, pd.DataFrame]
) -> dict:
    """单股趋势结构检查（从内存缓存读取，无DB访问）"""
    result = {
        'passed': False,
        'score_bonus': 0.0,
        'details': {},
        'reject_reason': '',
    }

    df = ohlcv_cache.get(ts_code)
    if df is None or len(df) < 5:
        result['reject_reason'] = '历史数据不足(<5天)'
        return result

    close = df['close'].dropna()
    if len(close) < 5:
        result['reject_reason'] = '有效数据不足(<5天)'
        return result

    close_now = close.iloc[-1]
    data_days = len(close)

    # 1. 周线CLOSE > 20周MA（≈100日线）
    # 数据不足100天时降级：用可用数据均线代替
    weekly_ma_period = cfg.layer3_weekly_ma_period * 5
    if data_days >= weekly_ma_period:
        ma_weekly = close.rolling(window=weekly_ma_period, min_periods=weekly_ma_period).mean()
        ma_weekly_now = ma_weekly.iloc[-1]
        result['details']['weekly_ma'] = round(ma_weekly_now, 2)
        result['details']['weekly_above'] = close_now > ma_weekly_now
        if close_now <= ma_weekly_now:
            result['reject_reason'] = '周线CLOSE≤20MA'
            return result
    elif data_days >= 10:
        # 降级：用全量数据均值近似
        ma_weekly_now = float(close.mean())
        result['details']['weekly_ma'] = round(ma_weekly_now, 2)
        result['details']['weekly_above'] = close_now > ma_weekly_now
        result['details']['weekly_ma_degraded'] = True
        if close_now <= ma_weekly_now:
            result['reject_reason'] = '收盘≤全量均价(数据不足100天)'
            return result
    else:
        # 数据极少，跳过周线检查
        result['details']['weekly_ma'] = 0
        result['details']['weekly_ma_degraded'] = True
        result['details']['weekly_ma_skipped'] = True

    # 2. EMA对齐检查（数据量自适应）
    fast = cfg.layer3_ema_fast
    mid = cfg.layer3_ema_mid
    slow = cfg.layer3_ema_slow

    ema12 = _calc_ema(close, fast)
    ema12_now = ema12.iloc[-1]
    result['details']['ema12'] = round(ema12_now, 2)

    if data_days >= slow:
        # 数据充足：完整 EMA12 > EMA26 > EMA50
        ema26 = _calc_ema(close, mid)
        ema50 = _calc_ema(close, slow)
        ema26_now = ema26.iloc[-1]
        ema50_now = ema50.iloc[-1]
        result['details']['ema26'] = round(ema26_now, 2)
        result['details']['ema50'] = round(ema50_now, 2)
        alignment_ok = ema12_now > ema26_now > ema50_now
        result['details']['ema_alignment'] = 'bullish' if alignment_ok else 'mixed'
        if not alignment_ok:
            result['reject_reason'] = 'EMA未多头排列(12>26>50)'
            return result
    elif data_days >= 5:
        # 数据不足：仅检查 EMA12 趋势方向（至少需当前>3天前）
        if data_days >= 4:
            ema12_3d_ago = ema12.iloc[-4] if len(ema12) > 3 else ema12.iloc[0]
            trending_up = ema12_now > ema12_3d_ago
        else:
            trending_up = close.iloc[-1] > close.iloc[0]
        result['details']['ema_alignment'] = 'bullish_short' if trending_up else 'mixed_short'
        result['details']['ema26'] = 0
        result['details']['ema50'] = 0
        if not trending_up:
            result['reject_reason'] = 'EMA12未呈上行趋势(数据不足)'
            return result

    # 3. 股价在EMA12上方
    if cfg.layer3_require_above_ema12:
        result['details']['close'] = round(close_now, 2)
        result['details']['above_ema12'] = close_now > ema12_now
        if close_now <= ema12_now:
            result['reject_reason'] = '股价≤EMA12'
            return result

    # 4. 年线加分（只有数据充足时才启用）
    annual_period = cfg.layer3_annual_ma_period
    if data_days >= annual_period:
        ma_annual = close.rolling(window=annual_period, min_periods=annual_period).mean()
        ma_annual_now = ma_annual.iloc[-1]
        result['details']['annual_ma'] = round(ma_annual_now, 2)
        result['details']['above_annual'] = close_now > ma_annual_now
        if close_now > ma_annual_now:
            result['score_bonus'] += cfg.layer3_bonus_above_annual

    # 5. 趋势结构识别
    structure = _detect_trend_structure(df, cfg)
    result['details']['trend_structure'] = structure['structure']
    if structure['structure'] in cfg.layer3_trend_structure_modes:
        result['score_bonus'] += 2.0
    elif structure['structure'] == 'unknown' and len(cfg.layer3_trend_structure_modes) == 2:
        pass

    result['passed'] = True
    return result


def check_trend_structure(ts_code: str, trade_date: date, cfg, verbose: bool = False) -> dict:
    """单股趋势结构检查（兼容接口，内部用batch load）"""
    conn = get_db()
    try:
        cache = _batch_load_history([ts_code], trade_date, conn, days=300)
    finally:
        conn.close()
    return _check_single(ts_code, cfg, cache)


def run_layer3_trend_filter(
    stock_list: List[str],
    trade_date: date = None,
    cfg=None,
    verbose: bool = True,
) -> List[dict]:
    """
    趋势结构过滤，返回通过过滤的股票详情列表。

    优化: 批量加载OHLCV(1次SQL) + ThreadPoolExecutor并行分析。
    """
    if cfg is None:
        from .funnel_config import DEFAULT_FUNNEL_CONFIG
        cfg = DEFAULT_FUNNEL_CONFIG

    if not cfg.layer3_enabled:
        return [{'ts_code': c} for c in stock_list]

    if trade_date is None:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
        row = cur.fetchone()
        trade_date = row['max_date'] if row else date.today()
        cur.close()
        conn.close()

    n_total = len(stock_list)

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  [Layer 3] 趋势结构过滤  — 待筛选 {n_total} 只")
        print(f"{'─'*60}")
        print(f"  周线>20MA  EMA{cfg.layer3_ema_fast}>{cfg.layer3_ema_mid}>{cfg.layer3_ema_slow}  "
              f"股价>EMA{cfg.layer3_ema_fast}  结构: {cfg.layer3_trend_structure_modes}")

    # ── 阶段1: 批量加载 OHLCV（1 次 SQL）──
    conn = get_db()
    try:
        if verbose:
            print(f"  ⏳ 批量加载K线数据 ({n_total} 只)...")
        ohlcv_cache = _batch_load_history(stock_list, trade_date, conn, days=300)
    finally:
        conn.close()

    if verbose:
        print(f"  ✓ 数据加载完成: {len(ohlcv_cache)}/{n_total} 只有效K线")

    # ── 阶段2: 并行分析 ──
    passed = []
    reject_stats = {'周线MA': 0, 'EMA排列': 0, '股价位置': 0, '数据不足': 0}

    if len(stock_list) > 100:
        _run_parallel(stock_list, cfg, ohlcv_cache, passed, reject_stats, verbose)
    else:
        _run_serial(stock_list, cfg, ohlcv_cache, passed, reject_stats, verbose)

    if verbose:
        print(f"  ✓ 通过: {len(passed)} 只")
        print(f"  ✗ 淘汰: {n_total - len(passed)} 只")
        for reason, count in reject_stats.items():
            if count > 0:
                print(f"    {reason}: {count} 只")

    return passed


def _run_parallel(stock_list, cfg, ohlcv_cache, passed, reject_stats, verbose):
    """多线程并行分析（>100只时启用）"""
    n_total = len(stock_list)
    passed_codes = set()

    with ThreadPoolExecutor(max_workers=LAYER3_WORKERS) as executor:
        futures = {
            executor.submit(_check_single, ts_code, cfg, ohlcv_cache): ts_code
            for ts_code in stock_list
        }

        completed = 0
        for future in as_completed(futures):
            completed += 1
            if verbose and not HAS_TQDM and completed % 100 == 0:
                print(f"  进度: {completed}/{n_total}")

            ts_code = futures[future]
            check = future.result()
            if check['passed']:
                passed_codes.add(ts_code)
                passed.append({
                    'ts_code': ts_code,
                    'score_bonus': check['score_bonus'],
                    'details': check['details'],
                })
            else:
                reason = check['reject_reason']
                if '周线' in reason:
                    reject_stats['周线MA'] += 1
                elif 'EMA' in reason:
                    reject_stats['EMA排列'] += 1
                elif '股价' in reason:
                    reject_stats['股价位置'] += 1
                elif '不足' in reason:
                    reject_stats['数据不足'] += 1

    if passed_codes:
        ordered = sorted(passed, key=lambda x: stock_list.index(x['ts_code']))
        passed.clear()
        passed.extend(ordered)


def _run_serial(stock_list, cfg, ohlcv_cache, passed, reject_stats, verbose):
    """串行分析（≤100只时使用）"""
    n_total = len(stock_list)
    for i, ts_code in enumerate(stock_list):
        if verbose and (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{n_total}")

        check = _check_single(ts_code, cfg, ohlcv_cache)
        if check['passed']:
            passed.append({
                'ts_code': ts_code,
                'score_bonus': check['score_bonus'],
                'details': check['details'],
            })
        else:
            reason = check['reject_reason']
            if '周线' in reason:
                reject_stats['周线MA'] += 1
            elif 'EMA' in reason:
                reject_stats['EMA排列'] += 1
            elif '股价' in reason:
                reject_stats['股价位置'] += 1
            elif '不足' in reason:
                reject_stats['数据不足'] += 1
