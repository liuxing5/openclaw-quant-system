"""
数据访问层
===========
v4: 极致优化回测速度
- 预加载时按股票分组构建索引字典，O(1)查找
- 预加载包含历史数据（B层需要60日均线）
- 一次性预计算所有B/C/D层技术指标（向量化groupby+rolling）
- 按日期构建指标快照索引，每日扫描变为简单DataFrame过滤
- 收盘价查找表直接用dict，无pandas开销
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
        self._preloaded_by_code: Optional[Dict[str, pd.DataFrame]] = None  # {ts_code: DataFrame}
        self._preloaded_close_map: Optional[Dict[str, Dict[str, float]]] = None
        self._preloaded_main_flow: Optional[pd.DataFrame] = None
        self._preloaded_main_flow_by_code: Optional[Dict[str, pd.DataFrame]] = None
        # 快照索引：{trade_date: DataFrame}
        self._snapshot_cache: Dict[str, pd.DataFrame] = {}
        # 预计算指标：{trade_date: DataFrame}（含所有B/C/D层指标）
        self._indicators_df: Optional[pd.DataFrame] = None
        self._indicators_by_date: Dict[str, pd.DataFrame] = {}

    # ----------------------------------------------------------
    # 批量预加载（回测专用）
    # ----------------------------------------------------------
    def preload_for_backtest(self, start_date: str, end_date: str):
        """一次性加载回测区间全量日线数据到内存，并构建索引

        注意：调用方应负责扩展start_date以包含足够历史数据（B层需要60日均线等）
        """
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
                n_stocks = self._preloaded_daily['ts_code'].nunique()
                logger.info(f"  预加载日线: {len(self._preloaded_daily)} 条, {n_stocks} 只股票")

                # 按股票分组构建索引（核心优化：O(1)查找）
                logger.info("  构建股票索引...")
                self._preloaded_by_code = {
                    code: grp.reset_index(drop=True)
                    for code, grp in self._preloaded_daily.groupby('ts_code', sort=False)
                }

                # 构建收盘价快速查找表
                logger.info("  构建收盘价查找表...")
                self._preloaded_close_map = {}
                for code, grp in self._preloaded_by_code.items():
                    self._preloaded_close_map[code] = dict(
                        zip(grp['trade_date'], grp['close'].astype(float))
                    )

                # 构建快照索引
                logger.info("  构建快照索引...")
                self._snapshot_cache = {
                    date_val: grp.reset_index(drop=True)
                    for date_val, grp in self._preloaded_daily.groupby('trade_date', sort=False)
                }

                logger.info("  索引构建完成")
            else:
                self._preloaded_daily = pd.DataFrame()
                self._preloaded_by_code = {}
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
                    self._preloaded_main_flow_by_code = {}
                    for code, grp in self._preloaded_main_flow.groupby('ts_code'):
                        self._preloaded_main_flow_by_code[code] = grp.reset_index(drop=True)
                    logger.info(f"  预加载主力资金: {len(self._preloaded_main_flow)} 条")
            except Exception:
                self._preloaded_main_flow = None
                self._preloaded_main_flow_by_code = None

        except Exception as e:
            logger.error(f"预加载失败: {e}")
            self._preloaded_daily = pd.DataFrame()
            self._preloaded_by_code = {}
            self._preloaded_close_map = {}
        finally:
            if conn and not conn.closed:
                conn.close()

        # 预计算所有技术指标
        self._precompute_indicators()

    # ----------------------------------------------------------
    # 预计算技术指标（核心优化：向量化计算所有B/C/D层指标）
    # ----------------------------------------------------------
    def _precompute_indicators(self):
        """一次性向量化计算所有B/C/D层技术指标"""
        if self._preloaded_daily is None or self._preloaded_daily.empty:
            return

        import time
        t0 = time.time()
        logger.info("  预计算技术指标...")

        # 直接在原DataFrame上操作，避免copy开销
        df = self._preloaded_daily

        # 确保按股票和日期排序
        df.sort_values(['ts_code', 'trade_date'], inplace=True)
        df.reset_index(drop=True, inplace=True)

        # 数值类型转换（一次性）
        for col in ['close', 'high', 'low', 'volume', 'amount', 'pct_chg', 'turnover_rate']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # ---- B层指标 ----
        t1 = time.time()
        logger.info("    B1: 量能指标...")
        g = df.groupby('ts_code', sort=False)
        df['vol_ma_60'] = g['amount'].transform(
            lambda x: x.rolling(60, min_periods=60).mean()
        )
        df['vol_breakout_ratio'] = df['amount'] / df['vol_ma_60'].replace(0, np.nan)

        logger.info("    B2: 价格突破指标...")
        df['box_60_high'] = g['high'].transform(
            lambda x: x.rolling(60, min_periods=60).max().shift(1)
        )
        df['box_60_low'] = g['low'].transform(
            lambda x: x.rolling(60, min_periods=60).min().shift(1)
        )
        df['ma_120'] = g['close'].transform(
            lambda x: x.rolling(120, min_periods=120).mean()
        )
        df['above_ma_120_pct'] = (df['close'] - df['ma_120']) / df['ma_120'].replace(0, np.nan)

        logger.info("    B3: 主力资金指标...")
        if self._preloaded_main_flow is not None:
            flow = self._preloaded_main_flow[['ts_code', 'trade_date', 'main_net_inflow']].drop_duplicates(
                subset=['ts_code', 'trade_date']
            )
            df.merge(flow, on=['ts_code', 'trade_date'], how='left', inplace=True)
        else:
            df['main_net_inflow'] = np.nan
        df['main_inflow_ratio'] = df['main_net_inflow'] / df['amount'].replace(0, np.nan)

        # ---- C层指标 ----
        logger.info("    C层指标...")
        df['amount_ma_20'] = g['amount'].transform(
            lambda x: x.rolling(20, min_periods=20).mean().shift(1)
        )
        df['amount_ratio_20'] = df['amount'] / df['amount_ma_20'].replace(0, np.nan)
        df['prev_volume'] = g['volume'].shift(1)
        df['volume_shrink_ratio'] = df['volume'] / df['prev_volume'].replace(0, np.nan)

        # ---- D层指标 ----
        logger.info("    D层指标...")
        df['volume_ma_20'] = g['volume'].transform(
            lambda x: x.rolling(20, min_periods=20).mean().shift(1)
        )
        df['volume_ratio_20'] = df['volume'] / df['volume_ma_20'].replace(0, np.nan)
        df['seal_quality_est'] = df['amount'] / df['vol_ma_60'].replace(0, np.nan) * 0.001

        # ---- 辅助列 ----
        df['is_kcb_cyb'] = df['ts_code'].str.startswith(('300', '688'))

        # ---- C5: 行业统计 ----
        logger.info("    行业统计...")
        if 'industry' in df.columns and df['industry'].notna().any():
            industry_stats = df.groupby(['trade_date', 'industry'], sort=False).agg(
                industry_avg_pct=('pct_chg', 'mean'),
                industry_count=('ts_code', 'count'),
                industry_rising_count=('pct_chg', lambda x: (x.dropna() > 0).sum())
            ).reset_index()
            df.merge(industry_stats, on=['trade_date', 'industry'], how='left', inplace=True)
        else:
            df['industry_avg_pct'] = np.nan
            df['industry_count'] = 0
            df['industry_rising_count'] = 0

        # 存储指标数据
        self._indicators_df = df

        # 按日期构建索引
        self._indicators_by_date = {}
        for date_val, grp in df.groupby('trade_date', sort=False):
            self._indicators_by_date[date_val] = grp.reset_index(drop=True)

        t2 = time.time()
        n_indicators = len([c for c in df.columns if c not in
                            ['ts_code', 'trade_date', 'open', 'close', 'high', 'low',
                             'volume', 'amount', 'pct_chg', 'turnover_rate', 'industry', 'name']])
        logger.info(f"  预计算完成: {n_indicators} 个指标列, {len(df)} 条记录, {len(self._indicators_by_date)} 个交易日, 耗时{t2-t0:.1f}s")

    def get_indicators_snapshot(self, trade_date: str) -> pd.DataFrame:
        """获取某日的预计算指标快照"""
        return self._indicators_by_date.get(trade_date, pd.DataFrame())

    def get_preloaded_close(self, ts_code: str, trade_date: str) -> Optional[float]:
        """从预加载数据快速获取收盘价 - O(1)"""
        if self._preloaded_close_map and ts_code in self._preloaded_close_map:
            return self._preloaded_close_map[ts_code].get(trade_date)
        return None

    def get_preloaded_daily(self, ts_code: str, start_date: str = "2020-01-01",
                            end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """从预加载数据获取单只股票日线 - O(1)查找+日期过滤"""
        if self._preloaded_by_code is None or ts_code not in self._preloaded_by_code:
            return None
        df = self._preloaded_by_code[ts_code]
        mask = pd.Series(True, index=df.index)
        if start_date:
            mask = mask & (df['trade_date'] >= start_date)
        if end_date:
            mask = mask & (df['trade_date'] <= end_date)
        filtered = df[mask]
        if len(filtered) == 0:
            return None
        return filtered.reset_index(drop=True)

    def get_preloaded_snapshot(self, trade_date: str, min_amount: float = 1e8) -> pd.DataFrame:
        """从预加载数据获取全市场快照 - O(1)查找"""
        if trade_date in self._snapshot_cache:
            snap = self._snapshot_cache[trade_date]
            if min_amount > 0:
                snap = snap[snap['amount'] > min_amount]
            return snap[snap['pct_chg'].notna()].reset_index(drop=True) if not snap.empty else pd.DataFrame()
        return pd.DataFrame()

    def get_preloaded_main_flow(self, ts_code: str, start_date: str = "2025-01-01",
                                end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """从预加载数据获取主力资金流 - O(1)查找"""
        if self._preloaded_main_flow_by_code is None or ts_code not in self._preloaded_main_flow_by_code:
            return None
        df = self._preloaded_main_flow_by_code[ts_code]
        mask = pd.Series(True, index=df.index)
        if start_date:
            mask = mask & (df['trade_date'] >= start_date)
        if end_date:
            mask = mask & (df['trade_date'] <= end_date)
        filtered = df[mask]
        return filtered if not filtered.empty else None

    # ----------------------------------------------------------
    # 日线行情（带预加载优先）
    # ----------------------------------------------------------
    def get_daily(self, ts_code: str, start_date: str = "2020-01-01",
                  end_date: Optional[str] = None,
                  min_days: int = 100) -> Optional[pd.DataFrame]:
        # 优先使用预加载数据
        if self._preloaded_by_code is not None and ts_code in self._preloaded_by_code:
            df = self.get_preloaded_daily(ts_code, start_date, end_date)
            if df is not None and len(df) > 0:
                return df

        # 使用宽范围缓存
        cache_key = f"{ts_code}:daily_full"
        if cache_key in self._cache:
            full_df = self._cache[cache_key]
            mask = pd.Series(True, index=full_df.index)
            if start_date:
                mask = mask & (full_df['trade_date'].astype(str) >= start_date)
            if end_date:
                mask = mask & (full_df['trade_date'].astype(str) <= end_date)
            filtered = full_df[mask].reset_index(drop=True)
            if len(filtered) > 0:
                return filtered

        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
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
            self._cache[cache_key] = full_df

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
        # 优先使用预加载快照
        if self._snapshot_cache:
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
    # 分时数据（1分钟线）— 回测模式下不使用
    # ----------------------------------------------------------
    def get_1min_kline(self, ts_code: str, trade_date: str) -> Optional[pd.DataFrame]:
        return None  # 回测模式直接跳过

    # ----------------------------------------------------------
    # 主力资金流（带预加载优先）
    # ----------------------------------------------------------
    def get_main_force_flow(self, ts_code: str,
                            start_date: str = "2025-01-01",
                            end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        if self._preloaded_main_flow_by_code is not None:
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
        if self._snapshot_cache:
            days = sorted(self._snapshot_cache.keys())
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
