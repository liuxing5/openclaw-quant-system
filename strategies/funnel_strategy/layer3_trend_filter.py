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
from datetime import date, timedelta
from typing import List, Dict, Optional

import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db


def _calc_ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False).mean()


def _load_history(ts_code: str, trade_date: date, days: int = 350) -> Optional[pd.DataFrame]:
    """加载历史K线"""
    start_date = trade_date - timedelta(days=days)
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT trade_date, open, high, low, close, volume
            FROM daily_quotes
            WHERE ts_code = %s AND trade_date >= %s AND trade_date <= %s
            ORDER BY trade_date ASC;
        """, (ts_code, start_date, trade_date))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return None

        df = pd.DataFrame(rows)
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        df.set_index('trade_date', inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df
    except Exception:
        return None


def _detect_trend_structure(df: pd.DataFrame, cfg) -> dict:
    """
    识别趋势结构：上升平台 / 回踩支撑 / 未知
    """
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


def check_trend_structure(
    ts_code: str,
    trade_date: date,
    cfg,
    verbose: bool = False,
) -> dict:
    """
    单股趋势结构检查。
    
    返回:
      {
        'passed': bool,
        'score_bonus': float,
        'details': dict,
        'reject_reason': str,
      }
    """
    result = {
        'passed': False,
        'score_bonus': 0.0,
        'details': {},
        'reject_reason': '',
    }

    df = _load_history(ts_code, trade_date, days=350)
    if df is None or len(df) < 50:
        result['reject_reason'] = '历史数据不足'
        return result

    close = df['close'].dropna()
    if len(close) < 50:
        result['reject_reason'] = '有效数据不足'
        return result

    # 1. 周线CLOSE > 20周MA（≈100日线）
    weekly_ma_period = cfg.layer3_weekly_ma_period * 5  # 周线转日线
    if len(close) >= weekly_ma_period:
        ma_weekly = close.rolling(window=weekly_ma_period, min_periods=weekly_ma_period).mean()
        close_now = close.iloc[-1]
        ma_weekly_now = ma_weekly.iloc[-1]
        result['details']['weekly_ma'] = round(ma_weekly_now, 2)
        result['details']['weekly_above'] = close_now > ma_weekly_now

        if close_now <= ma_weekly_now:
            result['reject_reason'] = '周线CLOSE≤20MA'
            return result
    else:
        result['reject_reason'] = '数据不足以计算周线MA'
        return result

    # 2. EMA12 > EMA26 > EMA50
    ema12 = _calc_ema(close, cfg.layer3_ema_fast)
    ema26 = _calc_ema(close, cfg.layer3_ema_mid)
    ema50 = _calc_ema(close, cfg.layer3_ema_slow)

    ema12_now = ema12.iloc[-1]
    ema26_now = ema26.iloc[-1]
    ema50_now = ema50.iloc[-1]

    result['details']['ema12'] = round(ema12_now, 2)
    result['details']['ema26'] = round(ema26_now, 2)
    result['details']['ema50'] = round(ema50_now, 2)
    result['details']['ema_alignment'] = 'bullish' if ema12_now > ema26_now > ema50_now else 'mixed'

    if not (ema12_now > ema26_now > ema50_now):
        result['reject_reason'] = 'EMA未多头排列(12>26>50)'
        return result

    # 3. 股价在EMA12上方
    if cfg.layer3_require_above_ema12:
        close_now = close.iloc[-1]
        result['details']['close'] = round(close_now, 2)
        result['details']['above_ema12'] = close_now > ema12_now
        if close_now <= ema12_now:
            result['reject_reason'] = '股价≤EMA12'
            return result

    # 4. 年线加分（股价>200EMA即可，不强制）
    annual_period = cfg.layer3_annual_ma_period
    if len(close) >= annual_period:
        ma_annual = close.rolling(window=annual_period, min_periods=annual_period).mean()
        close_now = close.iloc[-1]
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
        # 如果要求两个结构模式但没有命中任一，仍然通过（不强制）
        pass

    result['passed'] = True
    return result


def run_layer3_trend_filter(
    stock_list: List[str],
    trade_date: date = None,
    cfg=None,
    verbose: bool = True,
) -> List[dict]:
    """
    趋势结构过滤，返回通过过滤的股票详情列表。
    每项包含: {ts_code, passed, score_bonus, details}
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

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  [Layer 3] 趋势结构过滤  — 待筛选 {len(stock_list)} 只")
        print(f"{'─'*60}")
        print(f"  周线>20MA  EMA{cfg.layer3_ema_fast}>{cfg.layer3_ema_mid}>{cfg.layer3_ema_slow}  "
              f"股价>EMA{cfg.layer3_ema_fast}  结构: {cfg.layer3_trend_structure_modes}")

    passed = []
    reject_stats = {'周线MA': 0, 'EMA排列': 0, '股价位置': 0}

    for i, ts_code in enumerate(stock_list):
        if verbose and (i + 1) % 100 == 0:
            print(f"  进度: {i+1}/{len(stock_list)}")

        check = check_trend_structure(ts_code, trade_date, cfg, verbose=False)

        if check['passed']:
            item = {'ts_code': ts_code, 'score_bonus': check['score_bonus'], 'details': check['details']}
            passed.append(item)
        else:
            reason = check['reject_reason']
            if '周线' in reason:
                reject_stats['周线MA'] += 1
            elif 'EMA' in reason:
                reject_stats['EMA排列'] += 1
            elif '股价' in reason:
                reject_stats['股价位置'] += 1

    if verbose:
        print(f"  ✓ 通过: {len(passed)} 只")
        print(f"  ✗ 淘汰: {len(stock_list) - len(passed)} 只")
        for reason, count in reject_stats.items():
            if count > 0:
                print(f"    {reason}: {count} 只")

    return passed
