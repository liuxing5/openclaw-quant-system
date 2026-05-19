"""
数据访问层
===========
统一数据源：Supabase 数据库（daily_quotes）+ baostock（补充财务/行业数据）
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd
from psycopg2.extras import RealDictCursor

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db_fresh

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class DataLoader:
    """从 Supabase daily_quotes 加载行情数据"""

    def __init__(self, cache_dir: Optional[str] = None):
        self._cache: Dict[str, pd.DataFrame] = {}

    # ----------------------------------------------------------
    # 日线行情
    # ----------------------------------------------------------
    def get_daily(self, ts_code: str, start_date: str = "2020-01-01",
                  end_date: Optional[str] = None,
                  min_days: int = 100) -> Optional[pd.DataFrame]:
        cache_key = f"{ts_code}:{start_date}:{end_date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if end_date:
                cur.execute("""
                    SELECT trade_date, open, high, low, close, volume, amount,
                           pct_chg, turnover_rate
                    FROM daily_quotes
                    WHERE ts_code = %s
                      AND trade_date >= %s
                      AND trade_date <= %s
                    ORDER BY trade_date
                """, (ts_code, start_date, end_date))
            else:
                cur.execute("""
                    SELECT trade_date, open, high, low, close, volume, amount,
                           pct_chg, turnover_rate
                    FROM daily_quotes
                    WHERE ts_code = %s
                      AND trade_date >= %s
                    ORDER BY trade_date
                """, (ts_code, start_date))
            rows = cur.fetchall()
            cur.close()
            if not rows or len(rows) < min_days:
                return None
            df = pd.DataFrame(rows)
            df = df.sort_values("trade_date").reset_index(drop=True)
            self._cache[cache_key] = df
            return df
        except Exception as e:
            logger.error(f"加载 {ts_code} 日线失败: {e}")
            return None
        finally:
            if conn and not conn.closed:
                conn.close()

    # ----------------------------------------------------------
    # 全市场日线快照（某日的所有标的）
    # ----------------------------------------------------------
    def get_market_snapshot(self, trade_date: str,
                            min_amount: float = 1e8) -> pd.DataFrame:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT ts_code, trade_date, open, high, low, close, volume, amount,
                       pct_chg, turnover_rate
                FROM daily_quotes
                WHERE trade_date = %s
                  AND amount > %s
                  AND pct_chg IS NOT NULL
                ORDER BY amount DESC
            """, (trade_date, min_amount))
            rows = cur.fetchall()
            cur.close()
            return pd.DataFrame(rows) if rows else pd.DataFrame()
        except Exception as e:
            logger.error(f"加载 {trade_date} 全市场快照失败: {e}")
            return pd.DataFrame()
        finally:
            if conn and not conn.closed:
                conn.close()

    # ----------------------------------------------------------
    # 分时数据（1分钟线）
    # ----------------------------------------------------------
    def get_1min_kline(self, ts_code: str, trade_date: str) -> Optional[pd.DataFrame]:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT time, open, high, low, close, volume, amount
                FROM kline_1min
                WHERE ts_code = %s
                  AND trade_date = %s
                ORDER BY time
            """, (ts_code, trade_date))
            rows = cur.fetchall()
            cur.close()
            if not rows:
                return None
            df = pd.DataFrame(rows)
            return df
        except Exception:
            return None
        finally:
            if conn and not conn.closed:
                conn.close()

    # ----------------------------------------------------------
    # 主力资金流（从 daily_quotes 扩展字段/baostock）
    # ----------------------------------------------------------
    def get_main_force_flow(self, ts_code: str,
                            start_date: str = "2025-01-01",
                            end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if end_date:
                cur.execute("""
                    SELECT trade_date, main_net_inflow
                    FROM daily_quotes
                    WHERE ts_code = %s
                      AND trade_date >= %s
                      AND trade_date <= %s
                      AND main_net_inflow IS NOT NULL
                    ORDER BY trade_date
                """, (ts_code, start_date, end_date))
            else:
                cur.execute("""
                    SELECT trade_date, main_net_inflow
                    FROM daily_quotes
                    WHERE ts_code = %s
                      AND trade_date >= %s
                      AND main_net_inflow IS NOT NULL
                    ORDER BY trade_date
                """, (ts_code, start_date))
            rows = cur.fetchall()
            cur.close()
            if not rows:
                return None
            return pd.DataFrame(rows)
        except Exception:
            return None
        finally:
            if conn and not conn.closed:
                conn.close()

    # ----------------------------------------------------------
    # 交易日历
    # ----------------------------------------------------------
    def get_trading_days(self, start_date: str, end_date: str) -> List[str]:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT DISTINCT trade_date::text as date
                FROM daily_quotes
                WHERE trade_date >= %s
                  AND trade_date <= %s
                ORDER BY date
            """, (start_date, end_date))
            rows = cur.fetchall()
            cur.close()
            return [r['date'] for r in rows]
        except Exception:
            return []
        finally:
            if conn and not conn.closed:
                conn.close()

    # ----------------------------------------------------------
    # 清除缓存
    # ----------------------------------------------------------
    def clear_cache(self):
        self._cache.clear()