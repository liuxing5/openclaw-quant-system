"""
Layer 0: 大盘风控（盘前）
========================
决策逻辑：
  今日两市上涨≥2500家，且全A指数>20EMA。
  否则当日不荐股或仓位≤50%。

吸收策略：③看大盘控仓位

数据来源：
  - 上涨家数/下跌家数: daily_quotes 表直接 SQL 统计
  - 全A指数: 上证综指(000001.SH) daily_quotes 表
"""
from __future__ import annotations

import math
import sys
import os
from datetime import date, timedelta
from typing import Tuple

import pandas as pd
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db


def _fetch_market_breadth(trade_date=None) -> Tuple[int, int]:
    """
    从 daily_quotes 数据库统计两市上涨/下跌家数。
    返回 (上涨家数, 下跌家数)。
    数据库不可用时返回(0, 0)，由上层决定是否降级。
    """
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if trade_date is None:
            cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
            row = cur.fetchone()
            trade_date = row['max_date'] if row else date.today()
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE pct_chg > 0) as advancers,
                COUNT(*) FILTER (WHERE pct_chg < 0) as decliners
            FROM daily_quotes
            WHERE trade_date = %s;
        """, (trade_date,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return int(row['advancers'] or 0), int(row['decliners'] or 0)
    except Exception:
        pass
    return 0, 0


def _calc_ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False).mean()


def check_market_environment(
    trade_date: date = None,
    min_advancers: int = 2500,
    index_code: str = '000001.SH',
    ema_period: int = 20,
    partial_cap: float = 0.50,
    verbose: bool = True,
) -> dict:
    """
    大盘风控决策。
    
    返回:
      {
        'passed': bool,              # 全部条件通过
        'can_trade': bool,            # 是否可以交易（含部分仓位）
        'max_position_pct': float,   # 最大仓位比例
        'advancers': int,            # 上涨家数
        'decliners': int,            # 下跌家数
        'index_close': float,        # 指数收盘价
        'index_ema': float,          # 指数EMA
        'index_above_ema': bool,     # 指数是否在EMA上方
        'reason': str,              # 判定理由
      }
    """
    result = {
        'passed': False,
        'can_trade': False,
        'max_position_pct': 1.0,
        'advancers': 0,
        'decliners': 0,
        'index_close': 0.0,
        'index_ema': 0.0,
        'index_above_ema': False,
        'reason': '',
    }

    # 0. 解析交易日期
    if trade_date is None:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
        row = cur.fetchone()
        trade_date = row['max_date'] if row else date.today()
        cur.close()
        conn.close()

    # 1. 获取上涨家数（从数据库直接统计）
    advancers, decliners = _fetch_market_breadth(trade_date)
    result['advancers'] = advancers
    result['decliners'] = decliners

    breadth_ok = advancers >= min_advancers

    if verbose:
        print(f"  [Layer 0] 上涨: {advancers}  下跌: {decliners}  "
              f"要求上涨≥{min_advancers}")

    # 2. 获取全A指数（全市场等权均价）并计算EMA
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT trade_date, AVG(close) as market_close
        FROM daily_quotes
        WHERE trade_date >= %s AND trade_date <= %s
          AND close > 0
        GROUP BY trade_date
        ORDER BY trade_date ASC;
    """, (trade_date - timedelta(days=50), trade_date))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    index_above_ema = False
    index_close = 0.0
    index_ema = 0.0

    if rows:
        df = pd.DataFrame(rows)
        df.set_index('trade_date', inplace=True)
        df['close'] = pd.to_numeric(df['market_close'], errors='coerce')
        df = df.dropna(subset=['close'])

        index_close = float(df['close'].iloc[-1])
        if len(df) >= ema_period:
            ema_series = _calc_ema(df['close'], ema_period)
            index_ema = float(ema_series.iloc[-1])
            index_above_ema = index_close > index_ema
        elif len(df) >= 3:
            index_ema = float(df['close'].mean())
            index_above_ema = index_close > index_ema
        else:
            # 仅1-2天数据，保守假定不满足指数条件
            index_ema = index_close
            index_above_ema = False
        result['index_close'] = round(index_close, 2)
        result['index_ema'] = round(index_ema, 2)
        result['index_above_ema'] = index_above_ema

    result['index_close'] = round(index_close, 2)
    result['index_ema'] = round(index_ema, 2)
    result['index_above_ema'] = index_above_ema

    # 3. 综合判断
    if breadth_ok and index_above_ema:
        result['passed'] = True
        result['can_trade'] = True
        result['max_position_pct'] = 1.0
        result['reason'] = f'大盘偏强(涨{advancers}/指数>{ema_period}EMA)，满仓操作'
    elif breadth_ok or index_above_ema:
        result['passed'] = False
        result['can_trade'] = True
        result['max_position_pct'] = partial_cap
        result['reason'] = (f'大盘偏弱(涨{advancers}/指数{"上" if index_above_ema else "下"}穿{ema_period}EMA)，'
                           f'仓位≤{int(partial_cap*100)}%')
    else:
        result['passed'] = False
        result['can_trade'] = False
        result['max_position_pct'] = 0.0
        result['reason'] = f'大盘弱势(涨{advancers}/指数<{ema_period}EMA)，当日不荐股'

    if verbose:
        status = '✅全仓' if result['passed'] else ('⚠️半仓' if result['can_trade'] else '❌休战')
        print(f"    指数: {index_close:.2f}  {ema_period}EMA: {index_ema:.2f}  ➜  {status}")
        print(f"    {result['reason']}")

    return result
