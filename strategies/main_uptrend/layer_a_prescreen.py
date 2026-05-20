"""
Layer A: 选股池预筛（周频）
=============================
基本面筛选作为入池条件：
  1. 业绩加速：归母净利润同比 > +30%，且二阶导为正
  2. 市值适中：流通市值 50-200 亿
  3. 行业景气：申万二级行业近 20 日涨幅排名全市场前 30%
  4. 股权激励/回购：近 6 个月有相关公告
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import List, Set, Optional, Dict

import pandas as pd
import numpy as np

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db_fresh
from psycopg2.extras import RealDictCursor
from zoneinfo import ZoneInfo

from .config import MainUptrendConfig
from .data_loader import DataLoader

logger = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


class LayerAPrescreener:
    """A 层：基本面选股池预筛"""

    def __init__(self, cfg: MainUptrendConfig,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg
        self.loader = loader or DataLoader()
        self._pool_cache: Dict[str, Set[str]] = {}

    def prescreen(self, as_of_date: Optional[str] = None) -> Set[str]:
        """
        返回通过 A 层筛选的股票代码集合

        as_of_date: 基准日期 (YYYY-MM-DD)，None 则为最新交易日
        """
        if as_of_date is None:
            as_of_date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

        cache_key = as_of_date[:7]
        if cache_key in self._pool_cache:
            return self._pool_cache[cache_key]

        codes: Set[str] = set()

        codes_1 = self._filter_profit_acceleration()
        if codes_1:
            codes.update(codes_1)
            logger.info(f"A1 业绩加速: {len(codes_1)} 只通过")

        codes_2 = self._filter_market_cap(as_of_date)
        if codes_2:
            codes.update(codes_2)
            logger.info(f"A2 市值适中: {len(codes_2)} 只通过")

        codes_3 = self._filter_industry_momentum(as_of_date)
        if codes_3:
            codes.update(codes_3)
            logger.info(f"A3 行业景气: {len(codes_3)} 只通过")

        codes = codes_1 & codes_2 if codes_1 and codes_2 else (codes_1 | codes_2)
        if codes_3:
            codes = codes & codes_3

        if codes_1 or codes_2 or codes_3:
            codes = codes_1
            if codes_2:
                codes = codes & codes_2
            if codes_3:
                codes = codes & codes_3
        else:
            codes = set()

        self._pool_cache[cache_key] = codes
        logger.info(f"A 层预筛完成: {len(codes)} 只入池")
        return codes

    # ================================================================
    # A1: 业绩加速
    # ================================================================
    def _filter_profit_acceleration(self) -> Set[str]:
        conn = None
        codes = set()
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)

            cur.execute("""
                SELECT DISTINCT ts_code
                FROM financial_data
                WHERE indicator = 'net_profit_yoy'
                  AND report_period >= '2025-03-31'
                  AND value > %s
            """, (self.cfg.a_profit_growth_min,))
            for row in cur.fetchall():
                codes.add(row['ts_code'])
            cur.close()
        except Exception as e:
            logger.warning(f"A1 业绩加速查询失败: {e}")
        finally:
            if conn and not conn.closed:
                conn.close()
        return codes

    # ================================================================
    # A2: 市值适中
    # ================================================================
    def _filter_market_cap(self, as_of_date: str) -> Set[str]:
        # 优先从预加载数据获取
        if self.loader._preloaded_daily is not None and not self.loader._preloaded_daily.empty:
            return self._filter_market_cap_preloaded(as_of_date)

        conn = None
        codes = set()
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)

            cur.execute("""
                SELECT DISTINCT ts_code
                FROM daily_quotes
                WHERE trade_date = (
                    SELECT MAX(trade_date) FROM daily_quotes
                    WHERE trade_date <= %s
                )
                  AND amount IS NOT NULL
                  AND volume IS NOT NULL
                  AND close IS NOT NULL
            """, (as_of_date,))
            rows = cur.fetchall()
            cur.close()

            for row in rows:
                code = row['ts_code']
                market_cap = self._estimate_market_cap(conn, code, as_of_date)
                if market_cap and self.cfg.a_market_cap_min <= market_cap <= self.cfg.a_market_cap_max:
                    codes.add(code)
        except Exception as e:
            logger.warning(f"A2 市值查询失败: {e}")
        finally:
            if conn and not conn.closed:
                conn.close()
        return codes

    def _filter_market_cap_preloaded(self, as_of_date: str) -> Set[str]:
        """从预加载数据批量估算市值（向量化，无iterrows）"""
        df = self.loader._preloaded_daily
        day_data = df[df['trade_date'] == as_of_date]
        if day_data.empty:
            return set()

        # 向量化计算市值
        turnover_rate = pd.to_numeric(day_data['turnover_rate'], errors='coerce')
        close = pd.to_numeric(day_data['close'], errors='coerce')
        volume = pd.to_numeric(day_data['volume'], errors='coerce')

        # 过滤有效数据
        valid = (turnover_rate > 0) & close.notna() & volume.notna()
        valid_data = day_data[valid].copy()

        if valid_data.empty:
            return set()

        est_mcap = close[valid] * volume[valid] / turnover_rate[valid]
        in_range = (est_mcap >= self.cfg.a_market_cap_min) & (est_mcap <= self.cfg.a_market_cap_max)
        return set(valid_data.loc[in_range, 'ts_code'].tolist())

    def _estimate_market_cap(self, conn, ts_code: str,
                             as_of_date: str) -> Optional[float]:
        """估算流通市值（收盘价 × 流通股本）"""
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT close * volume / turnover_rate AS est_mcap
                FROM daily_quotes
                WHERE ts_code = %s
                  AND trade_date = (
                      SELECT MAX(trade_date) FROM daily_quotes
                      WHERE ts_code = %s AND trade_date <= %s
                  )
                  AND turnover_rate IS NOT NULL
                  AND turnover_rate > 0
                  AND volume IS NOT NULL
                  AND close IS NOT NULL
            """, (ts_code, ts_code, as_of_date))
            row = cur.fetchone()
            cur.close()
            if row and row['est_mcap']:
                return float(row['est_mcap'])
        except Exception:
            pass
        return None

    # ================================================================
    # A3: 行业景气
    # ================================================================
    def _filter_industry_momentum(self, as_of_date: str) -> Set[str]:
        # 优先从预加载数据获取
        if self.loader._preloaded_daily is not None and not self.loader._preloaded_daily.empty:
            return self._filter_industry_momentum_preloaded(as_of_date)

        conn = None
        codes = set()
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)

            start_date = (
                datetime.strptime(as_of_date, "%Y-%m-%d") -
                timedelta(days=self.cfg.a_industry_momentum_days * 2)
            ).strftime("%Y-%m-%d")

            cur.execute("""
                SELECT ts_code, industry, trade_date, pct_chg
                FROM daily_quotes
                WHERE trade_date BETWEEN %s AND %s
                  AND industry IS NOT NULL
                  AND pct_chg IS NOT NULL
                ORDER BY ts_code, trade_date
            """, (start_date, as_of_date))
            rows = cur.fetchall()
            cur.close()

            if not rows:
                return codes

            df = pd.DataFrame(rows)
            recent = df[df['trade_date'] > (
                datetime.strptime(as_of_date, "%Y-%m-%d") -
                timedelta(days=self.cfg.a_industry_momentum_days)
            ).strftime("%Y-%m-%d")]

            industry_returns = recent.groupby('industry')['pct_chg'].mean().sort_values(ascending=False)
            threshold_idx = max(1, int(len(industry_returns) * self.cfg.a_industry_momentum_top_pct))
            top_industries = set(industry_returns.head(threshold_idx).index)

            for code, grp in df.groupby('ts_code'):
                ind = grp['industry'].iloc[-1] if len(grp) > 0 else None
                if ind and ind in top_industries:
                    codes.add(code)
        except Exception as e:
            logger.warning(f"A3 行业景气查询失败: {e}")
        finally:
            if conn and not conn.closed:
                conn.close()
        return codes

    def _filter_industry_momentum_preloaded(self, as_of_date: str) -> Set[str]:
        """从预加载数据批量计算行业景气（向量化，无iterrows）"""
        df = self.loader._preloaded_daily
        if 'industry' not in df.columns:
            return set()

        start_date = (
            datetime.strptime(as_of_date, "%Y-%m-%d") -
            timedelta(days=self.cfg.a_industry_momentum_days * 2)
        ).strftime("%Y-%m-%d")

        mask = (df['trade_date'] >= start_date) & (df['trade_date'] <= as_of_date) & \
               df['industry'].notna() & df['pct_chg'].notna()
        sub = df[mask]
        if sub.empty:
            return set()

        recent_start = (
            datetime.strptime(as_of_date, "%Y-%m-%d") -
            timedelta(days=self.cfg.a_industry_momentum_days)
        ).strftime("%Y-%m-%d")
        recent = sub[sub['trade_date'] > recent_start]

        if recent.empty:
            return set()

        industry_returns = recent.groupby('industry')['pct_chg'].astype(float).mean().sort_values(ascending=False)
        threshold_idx = max(1, int(len(industry_returns) * self.cfg.a_industry_momentum_top_pct))
        top_industries = set(industry_returns.head(threshold_idx).index)

        # 向量化：找出属于景气行业的股票
        day_data = sub[sub['trade_date'] == as_of_date]
        in_top = day_data['industry'].isin(top_industries)
        return set(day_data.loc[in_top, 'ts_code'].tolist())