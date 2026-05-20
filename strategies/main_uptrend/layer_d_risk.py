"""
Layer D: 风险过滤
===================
提前发现不等于盲目追入，必须过滤掉以下风险：
  1. ST、退市风险警示股
  2. 近 30 日内有重大减持公告
  3. 诱多型涨停：量比 > 5 但封单 < 流通市值 0.3%
  4. 大股东质押比例 > 50% 的票，连板 > 3 后剔除
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Set

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
        self._st_cache: Optional[Set[str]] = None
        # 批量预加载缓存
        self._reduction_cache: Optional[Set[str]] = None  # 有减持的股票
        self._pledge_cache: Optional[Set[str]] = None     # 高质押股票
        self._preloaded_eval_date: Optional[str] = None

    def preload_for_date(self, eval_date: str):
        """批量预加载某日的风险数据，避免逐只股票查DB"""
        if self._preloaded_eval_date == eval_date:
            return
        self._preloaded_eval_date = eval_date

        # 批量加载ST
        if self._st_cache is None:
            self._st_cache = self._batch_load_st()

        # 批量加载减持
        self._reduction_cache = self._batch_load_reductions(eval_date)

        # 批量加载高质押
        if self._pledge_cache is None:
            self._pledge_cache = self._batch_load_high_pledge()

    def _batch_load_st(self) -> Set[str]:
        """批量加载ST/退市股票"""
        codes = set()
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT DISTINCT ts_code FROM daily_quotes
                WHERE name IS NOT NULL
                  AND (UPPER(name) LIKE '%%ST%%' OR name LIKE '%%*%%' OR name LIKE '%%退%%')
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
        """批量加载近期有减持的股票"""
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

    def check(self, ts_code: str, eval_date: str,
              consecutive_limits: int = 0) -> RiskVerdict:
        verdict = RiskVerdict(ts_code=ts_code, eval_date=eval_date)

        # D1: ST / 退市风险（使用缓存）
        if self.cfg.d_exclude_st or self.cfg.d_exclude_delist_warning:
            if self._st_cache is not None and ts_code in self._st_cache:
                verdict.passed = False
                verdict.blacklist_reasons.append("D1: ST/退市风险")
            elif self._st_cache is None:
                # 降级到逐只查询
                if self._is_st_or_delist(ts_code):
                    verdict.passed = False
                    verdict.blacklist_reasons.append("D1: ST/退市风险")

        # D2: 近 30 日重大减持（使用缓存）
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

        # D4: 高质押 + 连板 > 3（使用缓存）
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

    def _is_st_or_delist(self, ts_code: str) -> bool:
        if self._st_cache is not None and ts_code in self._st_cache:
            return True

        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT name FROM daily_quotes
                WHERE ts_code = %s AND name IS NOT NULL
                ORDER BY trade_date DESC LIMIT 1
            """, (ts_code,))
            row = cur.fetchone()
            cur.close()
            if row:
                name = (row['name'] or '').upper()
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

    def filter_list(self, ts_codes: List[str], eval_date: str,
                    consecutive_limits_map: Optional[dict] = None) -> List[str]:
        """
        批量过滤，返回通过 D 层的标的列表
        """
        # 预加载风险数据
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