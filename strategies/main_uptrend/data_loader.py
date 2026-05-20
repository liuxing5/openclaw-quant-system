"""
数据访问层
===========
统一数据源：Supabase 数据库（daily_quotes）+ baostock（补充财务/行业数据）

v2: 新增批量预加载接口，回测时一次性加载全量数据到内存，避免逐票逐日查DB
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
        # 批量预加载存储
        self._preloaded_daily: Optional[pd.DataFrame] = None
        self._preloaded_close_map: Optional[Dict[str, Dict[str, float]]] = None  # {ts_code: {date: close}}
        self._preloaded_main_flow: Optional[pd.DataFrame] = None

    # ----------------------------------------------------------
    # 批量预加载（回测专用）
    # ----------------------------------------------------------
    def preload_for_backtest(self, start_date: str, end_date: str):
        """一次性加载回测区间全量日线数据到内存"""
        logger.info(f"预加载回测数据 {start_date} ~ {end_date} ...")
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT ts_code, trade_date, open, high, low, close, volume, amount,
                       pct_chg, turnover_rate, industry, name
                FROM daily_quotes
                WHERE trade_date >= %s
                  AND trade_date <= %s
                  AND amount IS NOT NULL
                ORDER BY ts_code, trade_date
            """, (start_date, end_date))
            rows = cur.fetchall()
            cur.close()

            if rows:
                self._preloaded_daily = pd.DataFrame(rows)
                self._preloaded_daily['trade_date'] = self._preloaded_daily['trade_date'].astype(str)
                logger.info(f"  预加载日线: {len(self._preloaded_daily)} 条, {self._preloaded_daily['ts_code'].nunique()} 只股票")

                # 构建收盘价快速查找表（向量化）
                close_df = self._preloaded_daily[['ts_code', 'trade_date', 'close']].copy()
                self._preloaded_close_map = {}
                for code, grp in close_df.groupby('ts_code'):
                    self._preloaded_close_map[code] = dict(
                        zip(grp['trade_date'], grp['close'].astype(float))
                    )
            else:
                self._preloaded_daily = pd.DataFrame()
                self._preloaded_close_map = {}

            # 预加载主力资金流
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""
                    SELECT ts_code, trade_date, main_net_inflow
                    FROM daily_quotes
                    WHERE trade_date >= %s
                      AND trade_date <= %s
                      AND main_net_inflow IS NOT NULL
                    ORDER BY ts_code, trade_date
                """, (start_date, end_date))
                flow_rows = cur.fetchall()
                cur.close()
                if flow_rows:
                    self._preloaded_main_flow = pd.DataFrame(flow_rows)
                    self._preloaded_main_flow['trade_date'] = self._preloaded_main_flow['trade_date'].astype(str)
                    logger.info(f"  预加载主力资金: {len(self._preloaded_main_flow)} 条")
            except Exception:
                self._preloaded_main_flow = None

        except Exception as e:
            logger.error(f"预加载失败: {e}")
            self._preloaded_daily = pd.DataFrame()
            self._preloaded_close_map = {}
        finally:
            if conn and not conn.closed:
                conn.close()

    def get_preloaded_close(self, ts_code: str, trade_date: str) -> Optional[float]:
        """从预加载数据快速获取收盘价"""
        if self._preloaded_close_map and ts_code in self._preloaded_close_map:
            return self._preloaded_close_map[ts_code].get(trade_date)
        return None

    def get_preloaded_daily(self, ts_code: str, start_date: str = "2020-01-01",
                            end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """从预加载数据获取单只股票日线"""
        if self._preloaded_daily is None or self._preloaded_daily.empty:
            return None
        mask = self._preloaded_daily['ts_code'] == ts_code
        if start_date:
            mask = mask & (self._preloaded_daily['trade_date'] >= start_date)
        if end_date:
            mask = mask & (self._preloaded_daily['trade_date'] <= end_date)
        df = self._preloaded_daily[mask].copy()
        if len(df) == 0:
            return None
        return df.reset_index(drop=True)

    def get_preloaded_snapshot(self, trade_date: str, min_amount: float = 1e8) -> pd.DataFrame:
        """从预加载数据获取全市场快照"""
        if self._preloaded_daily is None or self._preloaded_daily.empty:
            return pd.DataFrame()
        mask = (self._preloaded_daily['trade_date'] == trade_date) & \
               (self._preloaded_daily['amount'] > min_amount) & \
               (self._preloaded_daily['pct_chg'].notna())
        df = self._preloaded_daily[mask].copy()
        return df if not df.empty else pd.DataFrame()

    def get_preloaded_main_flow(self, ts_code: str, start_date: str = "2025-01-01",
                                end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """从预加载数据获取主力资金流"""
        if self._preloaded_main_flow is None or self._preloaded_main_flow.empty:
            return None
        mask = self._preloaded_main_flow['ts_code'] == ts_code
        if start_date:
            mask = mask & (self._preloaded_main_flow['trade_date'] >= start_date)
        if end_date:
            mask = mask & (self._preloaded_main_flow['trade_date'] <= end_date)
        df = self._preloaded_main_flow[mask].copy()
        return df if not df.empty else None

    # ----------------------------------------------------------
    # 日线行情（带预加载优先）
    # ----------------------------------------------------------
    def get_daily(self, ts_code: str, start_date: str = "2020-01-01",
                  end_date: Optional[str] = None,
                  min_days: int = 100) -> Optional[pd.DataFrame]:
        # 优先使用预加载数据
        if self._preloaded_daily is not None and not self._preloaded_daily.empty:
            df = self.get_preloaded_daily(ts_code, start_date, end_date)
            if df is not None and len(df) >= min(min_days, 60):
                # 预加载有足够数据，直接返回
                return df
            if df is not None and len(df) > 0:
                # 预加载有部分数据，如果满足最低要求(60天)就返回
                # B层需要60日均线，60天数据足够
                return df

        # 使用宽范围缓存：同一个 ts_code 只缓存一份全量数据
        cache_key = f"{ts_code}:daily_full"
        if cache_key in self._cache:
            full_df = self._cache[cache_key]
            # 从缓存中筛选日期范围
            mask = pd.Series(True, index=full_df.index)
            if start_date:
                mask = mask & (full_df['trade_date'].astype(str) >= start_date)
            if end_date:
                mask = mask & (full_df['trade_date'].astype(str) <= end_date)
            filtered = full_df[mask].reset_index(drop=True)
            if len(filtered) >= min(min_days, 60):
                return filtered
            if len(filtered) > 0:
                return filtered

        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # 查询全量历史数据并缓存，后续查询复用
            cur.execute("""
                SELECT trade_date, open, high, low, close, volume, amount,
                       pct_chg, turnover_rate
                FROM daily_quotes
                WHERE ts_code = %s
                  AND trade_date >= '2020-01-01'
                ORDER BY trade_date
            """, (ts_code,))
            rows = cur.fetchall()
            cur.close()
            if not rows or len(rows) < min(min_days, 60):
                return None
            full_df = pd.DataFrame(rows)
            full_df = full_df.sort_values("trade_date").reset_index(drop=True)
            # 缓存全量数据
            self._cache[cache_key] = full_df

            # 筛选请求的日期范围
            mask = pd.Series(True, index=full_df.index)
            if start_date:
                mask = mask & (full_df['trade_date'].astype(str) >= start_date)
            if end_date:
                mask = mask & (full_df['trade_date'].astype(str) <= end_date)
            filtered = full_df[mask].reset_index(drop=True)
            return filtered if len(filtered) > 0 else None
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
        # 优先使用预加载数据
        if self._preloaded_daily is not None and not self._preloaded_daily.empty:
            snap = self.get_preloaded_snapshot(trade_date, min_amount)
            if not snap.empty:
                return snap

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
    # 主力资金流（带预加载优先）
    # ----------------------------------------------------------
    def get_main_force_flow(self, ts_code: str,
                            start_date: str = "2025-01-01",
                            end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        # 优先使用预加载数据
        if self._preloaded_main_flow is not None and not self._preloaded_main_flow.empty:
            df = self.get_preloaded_main_flow(ts_code, start_date, end_date)
            if df is not None:
                return df

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
        # 优先从预加载数据获取
        if self._preloaded_daily is not None and not self._preloaded_daily.empty:
            days = sorted(self._preloaded_daily['trade_date'].unique().tolist())
            return [d for d in days if start_date <= d <= end_date]

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