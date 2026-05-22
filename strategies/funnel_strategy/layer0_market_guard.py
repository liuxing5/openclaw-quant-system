"""
Layer 0: 大盘风控（盘前）
========================
决策逻辑：
  今日两市上涨≥2500家，且市场广度(上涨占比)>20EMA。
  否则当日不荐股或仓位≤50%。

吸收策略：③看大盘控仓位

数据来源：
  - 上涨家数/下跌家数: daily_quotes 表直接 SQL 统计
  - 市场广度EMA: 每日上涨占比的20日EMA（替代失效的指数EMA）
"""
from __future__ import annotations

import math
import sys
import os
from datetime import date, datetime, timezone, timedelta
from typing import Tuple

import pandas as pd
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db_fresh

BEIJING_TZ = timezone(timedelta(hours=8))


def _fetch_market_breadth(trade_date=None) -> Tuple[int, int]:
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if trade_date is None:
            cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
            row = cur.fetchone()
            trade_date = row['max_date'] if row else datetime.now(BEIJING_TZ).date()
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE pct_chg > 0) as advancers,
                COUNT(*) FILTER (WHERE pct_chg < 0) as decliners
            FROM daily_quotes
            WHERE trade_date = %s;
        """, (trade_date,))
        row = cur.fetchone()
        cur.close()
        if row:
            return int(row['advancers'] or 0), int(row['decliners'] or 0)
    except Exception as e:
        print(f"  ⚠️ Layer0 市场广度查询失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()
    return 0, 0


def _fetch_breadth_series(trade_date: date, days: int = 50) -> pd.DataFrame:
    """
    获取过去N天的市场广度时间序列（上涨家数占比）。
    用于计算广度EMA，替代失效的指数EMA。
    """
    start_date = trade_date - timedelta(days=days)
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT trade_date,
                   COUNT(*) FILTER (WHERE pct_chg > 0) as advancers,
                   COUNT(*) FILTER (WHERE pct_chg < 0) as decliners,
                   COUNT(*) as total
            FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s
            GROUP BY trade_date
            ORDER BY trade_date ASC;
        """, (start_date, trade_date))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        total_safe = df['total'].astype(float).replace(0, 1)
        df['breadth_ratio'] = df['advancers'].astype(float) / total_safe
        return df
    except Exception as e:
        print(f"  ⚠️ Layer0 广度时间序列查询失败: {e}")
        return pd.DataFrame()


def _calc_ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False).mean()


def check_market_environment(
    trade_date: date = None,
    min_advancers: int = 2000,
    min_breadth_ratio: float = 0.35,
    index_code: str = '000001.SH',
    ema_period: int = 20,
    partial_cap: float = 0.50,
    verbose: bool = True,
    use_breadth_ema: bool = True,
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
        'index_close': float,        # 广度占比%（替代原指数收盘价）
        'index_ema': float,          # 广度EMA%（替代原指数EMA）
        'index_above_ema': bool,     # 广度是否在EMA上方
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
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
            row = cur.fetchone()
            trade_date = row['max_date'] if row else datetime.now(BEIJING_TZ).date()
            cur.close()
        finally:
            if conn and not conn.closed:
                conn.close()

    # 1. 获取上涨家数（从数据库直接统计）
    advancers, decliners = _fetch_market_breadth(trade_date)
    result['advancers'] = advancers
    result['decliners'] = decliners

    breadth_ok = advancers >= min_advancers

    if verbose:
        print(f"  [Layer 0] 上涨: {advancers}  下跌: {decliners}  "
              f"要求上涨≥{min_advancers}")

    # 2. 计算市场广度EMA（替代失效的指数EMA）
    index_above_ema = False
    index_close = 0.0
    index_ema = 0.0

    if use_breadth_ema:
        breadth_df = _fetch_breadth_series(trade_date, days=50)

        if not breadth_df.empty and len(breadth_df) >= 3:
            today_breadth = float(breadth_df['breadth_ratio'].iloc[-1])
            index_close = round(today_breadth * 100, 2)

            if len(breadth_df) >= ema_period:
                ema_series = _calc_ema(breadth_df['breadth_ratio'], ema_period)
                breadth_ema_val = float(ema_series.iloc[-1])
                index_ema = round(breadth_ema_val * 100, 2)
                index_above_ema = today_breadth > breadth_ema_val
            elif len(breadth_df) >= 3:
                breadth_ema_val = float(breadth_df['breadth_ratio'].mean())
                index_ema = round(breadth_ema_val * 100, 2)
                index_above_ema = today_breadth > breadth_ema_val
            else:
                index_ema = index_close
                index_above_ema = False
        else:
            total_stocks = advancers + decliners
            if total_stocks > 0:
                today_breadth = advancers / total_stocks
                index_close = round(today_breadth * 100, 2)
                index_above_ema = today_breadth > 0.5
                index_ema = 50.0
    else:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT trade_date, close as market_close
                FROM daily_quotes
                WHERE ts_code = %s AND trade_date >= %s AND trade_date <= %s
                  AND close > 0
                ORDER BY trade_date ASC;
            """, (index_code, trade_date - timedelta(days=50), trade_date))
            rows = cur.fetchall()

            if not rows:
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
        except Exception as e:
            print(f"  ⚠️ Layer0 指数行情查询失败: {e}")
            rows = []
        finally:
            if conn and not conn.closed:
                conn.close()

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
                index_ema = index_close
                index_above_ema = False

    result['index_close'] = round(index_close, 2)
    result['index_ema'] = round(index_ema, 2)
    result['index_above_ema'] = index_above_ema

    # 3. 综合判断
    indicator_label = '广度' if use_breadth_ema else '指数'
    total_stocks = advancers + decliners
    breadth_ratio = advancers / total_stocks if total_stocks > 0 else 0

    # 满足上涨家数或广度占比任一条件即可
    breadth_condition = breadth_ok or (breadth_ratio >= min_breadth_ratio)

    if breadth_condition and index_above_ema:
        result['passed'] = True
        result['can_trade'] = True
        result['max_position_pct'] = 1.0
        result['reason'] = f'大盘偏强(涨{advancers}/{indicator_label}{index_close:.1f}%>{ema_period}EMA)，满仓操作'
    elif breadth_condition or index_above_ema:
        result['passed'] = False
        result['can_trade'] = True
        result['max_position_pct'] = partial_cap
        result['reason'] = (f'大盘偏弱(涨{advancers}/{indicator_label}{index_close:.1f}%{"上" if index_above_ema else "下"}穿{ema_period}EMA)，'
                           f'仓位≤{int(partial_cap*100)}%')
    else:
        result['passed'] = False
        result['can_trade'] = False
        result['max_position_pct'] = 0.0
        result['reason'] = f'大盘弱势(涨{advancers}/{indicator_label}{index_close:.1f}%<{ema_period}EMA)，当日不荐股'

    if verbose:
        status = '✅全仓' if result['passed'] else ('⚠️半仓' if result['can_trade'] else '❌休战')
        print(f"    {indicator_label}: {index_close:.1f}%  {ema_period}EMA: {index_ema:.1f}%  ➜  {status}")
        print(f"    {result['reason']}")

    return result
