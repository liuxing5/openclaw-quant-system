"""
Layer 6: 刚性风控
==================
决策逻辑：
  买入时段14:30后；初始止损=入场价-1ATR；
  次日移动止盈参考EMA12或分时VWAP；盈亏比≥2:1才允许推荐。

吸收策略：③固定时段  ⑦ATR止损/五日均线持仓/海龟风控比例化
"""
from __future__ import annotations

import math
import sys
import os
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict, Optional

import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db


BEIJING_TZ = timezone(timedelta(hours=8))


def _calc_ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False).mean()


def _calc_atr(df: pd.DataFrame, period: int = 20) -> float:
    """计算ATR（平均真实波幅），数据不足时自适应"""
    if len(df) < 3:
        return 0.0
    actual_period = min(period, max(2, len(df) - 1))

    high = df['high']
    low = df['low']
    close = df['close']

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(window=actual_period, min_periods=actual_period).mean().iloc[-1]
    return float(atr) if not pd.isna(atr) else 0.0


def _load_history(ts_code: str, trade_date: date, days: int = 60) -> Optional[pd.DataFrame]:
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


def check_time_window(cfg) -> dict:
    """
    检查当前时间是否在允许买入时段(14:30后)。
    
    返回:
      {
        'in_window': bool,
        'current_time': str,
        'entry_after': str,
        'message': str,
      }
    """
    now = datetime.now(BEIJING_TZ)
    current_time = now.strftime('%H:%M')
    entry_after = cfg.layer6_entry_after_time

    hour, minute = map(int, entry_after.split(':'))
    in_window = now.hour > hour or (now.hour == hour and now.minute >= minute)

    message = (f'当前{current_time}，{"在" if in_window else "不在"}买入时段(≥{entry_after})'
               if in_window else f'不在买入时段(当前{current_time}<{entry_after})')

    return {
        'in_window': in_window,
        'current_time': current_time,
        'entry_after': entry_after,
        'message': message,
    }


def _batch_load_history(
    stock_list: List[str], trade_date: date, db_conn, days: int = 60, verbose: bool = False
) -> Dict[str, pd.DataFrame]:
    """批量加载OHLCV历史数据"""
    if not stock_list:
        return {}
    start_date = trade_date - timedelta(days=days)
    result = {}
    try:
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
    except Exception as e:
        if verbose:
            print(f"  ⚠️ L6 批量数据加载失败: {e}")
    return result


def compute_risk_params(
    ts_code: str,
    entry_price: float,
    cfg,
    ohlcv_cache: Dict[str, pd.DataFrame],
) -> dict:
    """
    计算单只股票的ATR风控参数（从内存缓存读取）。
    
    返回:
      {
        'atr': float,
        'atr_pct': float,
        'stop_loss': float,
        'stop_loss_pct': float,
        'trailing_ref': float,
        'target_price': float,
        'profit_loss_ratio': float,
        'passed': bool,
      }
    """
    result = {
        'atr': 0.0,
        'atr_pct': 0.0,
        'stop_loss': 0.0,
        'stop_loss_pct': 0.0,
        'trailing_ref': 0.0,
        'target_price': 0.0,
        'profit_loss_ratio': 0.0,
        'passed': False,
    }

    df = ohlcv_cache.get(ts_code)
    if df is None or len(df) < 5:
        return result

    # ATR计算（自适应周期：数据不足时用可用数据）
    atr_period = min(cfg.layer6_atr_period, len(df) - 1)
    atr_period = max(atr_period, 5)  # 最少5天
    atr = _calc_atr(df, atr_period)
    if atr <= 0 or entry_price <= 0:
        return result

    result['atr'] = round(atr, 3)
    result['atr_pct'] = round(atr / entry_price * 100, 2)

    # 初始止损 = 入场价 - 1ATR
    stop_loss = entry_price - atr * cfg.layer6_initial_stop_atr
    result['stop_loss'] = round(stop_loss, 2)
    result['stop_loss_pct'] = round((entry_price - stop_loss) / entry_price * 100, 2)

    # 移动止盈参考价 (EMA12)
    ema12 = _calc_ema(df['close'], 12)
    result['trailing_ref'] = round(ema12.iloc[-1], 2) if len(ema12) > 0 else entry_price

    # 目标价 = 入场价 + 2ATR
    target_price = entry_price + atr * cfg.layer6_target_atr_mult
    result['target_price'] = round(target_price, 2)

    # 盈亏比
    risk = entry_price - stop_loss
    reward = target_price - entry_price
    result['profit_loss_ratio'] = round(reward / risk, 2) if risk > 0 else 0.0

    result['passed'] = result['profit_loss_ratio'] >= cfg.layer6_min_profit_loss_ratio

    return result


def run_layer6_risk_control(
    stock_items: List[dict],
    trade_date: date = None,
    cfg=None,
    verbose: bool = True,
) -> List[dict]:
    """
    刚性风控，返回通过风控的最终推荐列表。
    每项附加 {atr, stop_loss, target_price, profit_loss_ratio, ...}
    """
    if cfg is None:
        from .funnel_config import DEFAULT_FUNNEL_CONFIG
        cfg = DEFAULT_FUNNEL_CONFIG

    if not cfg.layer6_enabled:
        return stock_items

    if trade_date is None:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
        row = cur.fetchone()
        trade_date = row['max_date'] if row else datetime.now(BEIJING_TZ).date()
        cur.close()
        conn.close()

    # 时段检查
    time_check = check_time_window(cfg)

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  [Layer 6] 刚性风控  — 待检查 {len(stock_items)} 只")
        print(f"{'─'*60}")
        print(f"  时段: {time_check['message']}  "
              f"ATR周期: {cfg.layer6_atr_period}  止损: {cfg.layer6_initial_stop_atr}ATR  "
              f"盈亏比≥{cfg.layer6_min_profit_loss_ratio}:1")

    passed = []
    reject_stats = {'盈亏比不足': 0, 'ATR异常': 0}

    # 批量加载历史数据 + 收盘价
    stock_codes = [item['ts_code'] for item in stock_items]
    db_conn = get_db()
    try:
        ohlcv_cache = _batch_load_history(stock_codes, trade_date, db_conn, days=60, verbose=verbose)
    finally:
        db_conn.close()

    for item in stock_items:
        ts_code = item['ts_code']
        df = ohlcv_cache.get(ts_code)

        # 从缓存获取收盘价
        close_price = 0.0
        if df is not None and len(df) > 0:
            close_price = float(df['close'].iloc[-1])

        if close_price <= 0 or pd.isna(close_price):
            continue

        risk = compute_risk_params(ts_code, close_price, cfg, ohlcv_cache)
        item['atr'] = risk['atr']
        item['atr_pct'] = risk['atr_pct']
        item['stop_loss'] = risk['stop_loss']
        item['stop_loss_pct'] = risk['stop_loss_pct']
        item['trailing_ref'] = risk['trailing_ref']
        item['target_price'] = risk['target_price']
        item['profit_loss_ratio'] = risk['profit_loss_ratio']
        item['entry_price'] = round(close_price, 2)
        item['time_window_ok'] = time_check['in_window']

        if not risk['passed']:
            if risk['atr'] <= 0:
                reject_stats['ATR异常'] += 1
            else:
                reject_stats['盈亏比不足'] += 1
            continue

        passed.append(item)

    # 限制最终推荐数量
    passed = passed[:cfg.max_final_candidates]

    if verbose:
        print(f"  ✓ 通过: {len(passed)} 只 (最终推荐≤{cfg.max_final_candidates})")
        for reason, count in reject_stats.items():
            if count > 0:
                print(f"  ✗ {reason}: {count} 只")

    return passed
