"""
Layer D: 风险过滤
===================
提前发现不等于盲目追入，必须过滤掉以下风险：
  1. ST、退市风险警示股
  2. 近 30 日内有重大减持公告
  3. 诱多型涨停：量比 > 5 但封单 < 流通市值 0.3%
  4. 大股东质押比例 > 50% 的票，连板 > 3 后剔除

v2: 极致优化回测速度
- 一次性预加载全量风险数据（ST/减持/质押），避免每日查DB
- 向量化诱多检测，使用预计算指标替代逐只股票get_daily+创建LayerB实例
- filter_list_vectorized: 批量DataFrame过滤
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Set, Dict

import numpy as np
import pandas as pd

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


@dataclass
class RiskVerdict:
    """D 层单只股票的风险判定"""
    ts_code: str
    eval_date: str
    passed: bool = True
    blacklist_reasons: List[str] = field(default_factory=list)


class LayerDRiskFilter:
    """D 层：风险过滤"""

    def __init__(self, cfg: MainUptrendConfig,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg
        self.loader = loader or DataLoader()
        # 全量风险数据缓存（回测期间只加载一次）
        self._st_cache: Optional[Set[str]] = None
        self._reduction_cache: Optional[Set[str]] = None
        self._pledge_cache: Optional[Set[str]] = None
        # 回测模式标记
        self._backtest_mode: bool = False

    def preload_for_backtest(self, start_date: str, end_date: str):
        """回测开始前一次性加载全量风险数据，避免每日重复查DB"""
        self._backtest_mode = True
        logger.info("D层: 预加载全量风险数据...")

        # D1: ST/退市
        if self._st_cache is None:
            self._st_cache = self._batch_load_st()
            logger.info(f"  ST/退市: {len(self._st_cache)} 只")

        # D2: 减持（覆盖整个回测区间+30天）
        start_dt = datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=self.cfg.d_share_reduction_days)
        self._reduction_cache = self._batch_load_reductions_range(
            start_dt.strftime("%Y-%m-%d"), end_date
        )
        logger.info(f"  减持: {len(self._reduction_cache)} 只")

        # D4: 高质押
        if self._pledge_cache is None:
            self._pledge_cache = self._batch_load_high_pledge()
            logger.info(f"  高质押: {len(self._pledge_cache)} 只")

        logger.info("D层: 风险数据预加载完成")

    def preload_for_date(self, eval_date: str):
        """单日预加载（实盘模式用）"""
        if self._st_cache is None:
            self._st_cache = self._batch_load_st()
        if self._reduction_cache is None:
            self._reduction_cache = self._batch_load_reductions(eval_date)
        if self._pledge_cache is None:
            self._pledge_cache = self._batch_load_high_pledge()

    def filter_list(self, ts_codes: List[str], eval_date: str,
                    consecutive_limits_map: Optional[dict] = None) -> List[str]:
        """
        批量过滤，返回通过 D 层的标的列表

        优化：回测模式使用向量化过滤
        """
        if not ts_codes:
            return []

        # 回测模式：向量化过滤
        if self._backtest_mode and self.loader._indicators_by_date:
            return self._filter_list_vectorized(ts_codes, eval_date, consecutive_limits_map)

        # 实盘模式：逐只检查
        self.preload_for_date(eval_date)

        if consecutive_limits_map is None:
            consecutive_limits_map = {}
        passed = []
        rejected = 0
        for code in ts_codes:
            cons_limits = consecutive_limits_map.get(code, 0)
            verdict = self.check(code, eval_date, cons_limits)
            if verdict.passed:
                passed.append(code)
            else:
                rejected += 1
                logger.debug(f"D 层剔除 {code}: {verdict.blacklist_reasons}")
        logger.info(f"D 层过滤: {len(passed)} 通过 / {rejected} 剔除")
        return passed

    def _filter_list_vectorized(self, ts_codes: List[str], eval_date: str,
                                 consecutive_limits_map: Optional[dict] = None) -> List[str]:
        """向量化过滤：使用预计算指标批量检测，无iterrows"""
        if consecutive_limits_map is None:
            consecutive_limits_map = {}

        ind_df = self.loader.get_indicators_snapshot(eval_date)
        if ind_df.empty:
            return ts_codes  # 无指标数据时全部放行

        pool_df = ind_df[ind_df['ts_code'].isin(ts_codes)].copy()
        if pool_df.empty:
            return ts_codes

        # ---- D1: ST/退市 ----
        d1_fail = pd.Series(False, index=pool_df.index)
        if self._st_cache:
            d1_fail = pool_df['ts_code'].isin(self._st_cache)

        # ---- D2: 减持 ----
        d2_fail = pd.Series(False, index=pool_df.index)
        if self._reduction_cache:
            d2_fail = pool_df['ts_code'].isin(self._reduction_cache)

        # ---- D3: 诱多型涨停（向量化） ----
        pct_chg = pool_df['pct_chg'].fillna(0)
        is_kcb_cyb = pool_df.get('is_kcb_cyb', pd.Series(False, index=pool_df.index))
        limit_pct = np.where(is_kcb_cyb, 0.197, 0.097)
        is_zt = pct_chg >= limit_pct - 0.003

        vol_ratio = pool_df.get('volume_ratio_20', pd.Series(0, index=pool_df.index)).fillna(0)
        seal_quality = pool_df.get('seal_quality_est', pd.Series(0, index=pool_df.index)).fillna(0)
        d3_fail = is_zt & (vol_ratio > self.cfg.d_trap_volume_ratio) & \
                  (seal_quality < self.cfg.d_trap_seal_ratio_max)

        # ---- D4: 高质押+连板 ----
        d4_fail = pd.Series(False, index=pool_df.index)
        if self._pledge_cache:
            pledge_codes = pool_df['ts_code'].isin(self._pledge_cache)
            # 检查连板数
            if consecutive_limits_map:
                cons_limits_arr = pool_df['ts_code'].map(
                    lambda c: consecutive_limits_map.get(c, 0)
                ).fillna(0)
                d4_fail = pledge_codes & (cons_limits_arr > self.cfg.d_pledge_consecutive_limit_days)

        # ---- D5: 高位接盘风险 ----
        # 收盘价接近52周高点95%以上，剔除（避免高位接盘）
        high_52w = pool_df.get('high_52w', pd.Series(0, index=pool_df.index)).fillna(0)
        close = pool_df['close'].fillna(0)
        d5_fail = (high_52w > 0) & (close / high_52w > self.cfg.d_near_high_pct)

        # ---- D6: 接飞刀风险 ----
        # 近5日跌幅超过15%，剔除（避免接飞刀）
        pct_chg_5d = pool_df.get('pct_chg_5d', pd.Series(0, index=pool_df.index)).fillna(0)
        d6_fail = pct_chg_5d < -self.cfg.d_max_drop_5d

        # ---- D7: 追高风险 ----
        # 近20日涨幅超过30%，剔除（避免追高接盘）
        pct_chg_20d = pool_df.get('pct_chg_20d', pd.Series(0, index=pool_df.index)).fillna(0)
        d7_fail = pct_chg_20d > (self.cfg.d_max_gain_20d * 100)  # 转换为百分比

        # ---- 综合判定 ----
        all_pass = ~(d1_fail | d2_fail | d3_fail | d4_fail | d5_fail | d6_fail | d7_fail)
        passed_codes = pool_df.loc[all_pass, 'ts_code'].tolist()

        rejected = len(ts_codes) - len(passed_codes)
        logger.info(f"D层向量化过滤: {len(passed_codes)} 通过 / {rejected} 剔除")
        return passed_codes

    def check(self, ts_code: str, eval_date: str,
              consecutive_limits: int = 0) -> RiskVerdict:
        """单只股票风险检查（实盘模式用）"""
        verdict = RiskVerdict(ts_code=ts_code, eval_date=eval_date)

        # D1: ST / 退市风险
        if self.cfg.d_exclude_st or self.cfg.d_exclude_delist_warning:
            if self._st_cache is not None and ts_code in self._st_cache:
                verdict.passed = False
                verdict.blacklist_reasons.append("D1: ST/退市风险")
            elif self._st_cache is None:
                if self._is_st_or_delist(ts_code):
                    verdict.passed = False
                    verdict.blacklist_reasons.append("D1: ST/退市风险")

        # D2: 近 30 日重大减持
        if self._reduction_cache is not None:
            if ts_code in self._reduction_cache:
                verdict.passed = False
                verdict.blacklist_reasons.append("D2: 近30日有减持")
        else:
            if self._has_recent_reduction(ts_code, eval_date):
                verdict.passed = False
                verdict.blacklist_reasons.append("D2: 近30日有减持")

        # D3: 诱多型涨停
        if self._is_trap_limit_up(ts_code, eval_date):
            verdict.passed = False
            verdict.blacklist_reasons.append("D3: 诱多型涨停")

        # D4: 高质押 + 连板 > 3
        if consecutive_limits > self.cfg.d_pledge_consecutive_limit_days:
            if self._pledge_cache is not None:
                if ts_code in self._pledge_cache:
                    verdict.passed = False
                    verdict.blacklist_reasons.append("D4: 高质押连板")
            else:
                if self._is_high_pledge(ts_code, eval_date):
                    verdict.passed = False
                    verdict.blacklist_reasons.append("D4: 高质押连板")

        return verdict

    # ================================================================
    # 批量加载方法
    # ================================================================
    def _batch_load_st(self) -> Set[str]:
        """批量加载ST/退市股票"""
        codes = set()
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT DISTINCT d.ts_code FROM daily_quotes d
                LEFT JOIN stock_basic_info sb ON d.ts_code = sb.ts_code
                WHERE sb.stock_name IS NOT NULL
                  AND (UPPER(sb.stock_name) LIKE '%%ST%%' OR sb.stock_name LIKE '%%*%%' OR sb.stock_name LIKE '%%退%%')
            """)
            for row in cur.fetchall():
                codes.add(row['ts_code'])
            cur.close()
        except Exception:
            pass
        finally:
            if conn and not conn.closed:
                conn.close()
        return codes

    def _batch_load_reductions(self, eval_date: str) -> Set[str]:
        """批量加载近期有减持的股票（单日模式）"""
        codes = set()
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            start_date = (
                datetime.strptime(eval_date, "%Y-%m-%d") -
                timedelta(days=self.cfg.d_share_reduction_days)
            ).strftime("%Y-%m-%d")
            cur.execute("""
                SELECT DISTINCT ts_code FROM announcement_data
                WHERE pub_date BETWEEN %s AND %s
                  AND (title ILIKE '%%减持%%' OR title ILIKE '%%reduction%%')
            """, (start_date, eval_date))
            for row in cur.fetchall():
                codes.add(row['ts_code'])
            cur.close()
        except Exception:
            pass
        finally:
            if conn and not conn.closed:
                conn.close()
        return codes

    def _batch_load_reductions_range(self, start_date: str, end_date: str) -> Set[str]:
        """批量加载区间内所有有减持的股票（回测模式用）"""
        codes = set()
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT DISTINCT ts_code FROM announcement_data
                WHERE pub_date BETWEEN %s AND %s
                  AND (title ILIKE '%%减持%%' OR title ILIKE '%%reduction%%')
            """, (start_date, end_date))
            for row in cur.fetchall():
                codes.add(row['ts_code'])
            cur.close()
        except Exception:
            pass
        finally:
            if conn and not conn.closed:
                conn.close()
        return codes

    def _batch_load_high_pledge(self) -> Set[str]:
        """批量加载高质押股票"""
        codes = set()
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT DISTINCT ts_code FROM financial_data
                WHERE indicator = 'pledge_ratio'
                  AND value > %s
            """, (self.cfg.d_pledge_ratio_max,))
            for row in cur.fetchall():
                codes.add(row['ts_code'])
            cur.close()
        except Exception:
            pass
        finally:
            if conn and not conn.closed:
                conn.close()
        return codes

    # ================================================================
    # 逐只检查方法（实盘降级用）
    # ================================================================
    def _is_st_or_delist(self, ts_code: str) -> bool:
        if self._st_cache is not None and ts_code in self._st_cache:
            return True
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT sb.stock_name FROM stock_basic_info sb
                WHERE sb.ts_code = %s AND sb.stock_name IS NOT NULL
                LIMIT 1
            """, (ts_code,))
            row = cur.fetchone()
            cur.close()
            if row:
                name = (row['stock_name'] or '').upper()
                if 'ST' in name or '*' in name or '退' in name:
                    if self._st_cache is None:
                        self._st_cache = set()
                    self._st_cache.add(ts_code)
                    return True
        except Exception:
            pass
        finally:
            if conn and not conn.closed:
                conn.close()
        return False

    def _has_recent_reduction(self, ts_code: str, eval_date: str) -> bool:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            start_date = (
                datetime.strptime(eval_date, "%Y-%m-%d") -
                timedelta(days=self.cfg.d_share_reduction_days)
            ).strftime("%Y-%m-%d")
            cur.execute("""
                SELECT 1 FROM announcement_data
                WHERE ts_code = %s
                  AND pub_date BETWEEN %s AND %s
                  AND (title ILIKE '%%减持%%' OR title ILIKE '%%reduction%%')
                LIMIT 1
            """, (ts_code, start_date, eval_date))
            exists = cur.fetchone() is not None
            cur.close()
            return exists
        except Exception:
            return False
        finally:
            if conn and not conn.closed:
                conn.close()

    def _is_trap_limit_up(self, ts_code: str, eval_date: str) -> bool:
        """诱多型涨停检测（实盘模式用，回测模式用向量化版本）"""
        df = self.loader.get_daily(ts_code, start_date="2024-01-01", end_date=eval_date)
        if df is None or len(df) < 20:
            return False

        last = df.iloc[-1]
        pct = float(last["pct_chg"])

        from .indicators import detect_limit_up_pct, is_limit_up
        limit_pct = detect_limit_up_pct(ts_code)
        if not is_limit_up(pct, limit_pct):
            return False

        vol_ratio = (
            float(last["volume"]) / df["volume"].iloc[-21:-1].mean()
            if len(df) >= 21 and df["volume"].iloc[-21:-1].mean() > 0
            else 0
        )
        if vol_ratio <= self.cfg.d_trap_volume_ratio:
            return False

        from .layer_b_launch import LayerBLaunchDetector
        detector = LayerBLaunchDetector(self.cfg, self.loader)
        seal_score = detector._estimate_seal_quality(
            ts_code, eval_date, float(last["amount"])
        )
        return seal_score < self.cfg.d_trap_seal_ratio_max

    def _is_high_pledge(self, ts_code: str, eval_date: str) -> bool:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT 1 FROM financial_data
                WHERE ts_code = %s
                  AND indicator = 'pledge_ratio'
                  AND value > %s
                LIMIT 1
            """, (ts_code, self.cfg.d_pledge_ratio_max))
            exists = cur.fetchone() is not None
            cur.close()
            return exists
        except Exception:
            return False
        finally:
            if conn and not conn.closed:
                conn.close()
