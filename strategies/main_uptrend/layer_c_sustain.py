"""
Layer C: 持续性判定（日频）
===============================
判定"半天持续上涨"特征 — 跨日延续能力：
  1. 分时形态质量：日内上行占比 > 60%
  2. 大单买入占比：大单净买入 / 总成交额 > 8%
  3. 缩量上涨：价涨量缩（缩到前一日 60-80%）
  4. 板上量比：封板时间 + 开板次数
  5. 同板块联动：概念板块涨幅 > 3%，至少 2 只同行同涨
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set

import numpy as np
import pandas as pd

from .config import MainUptrendConfig
from .data_loader import DataLoader
from .indicators import (
    intraday_up_ratio, intraday_morning_checks,
    volume_shrink_check, sector_peers_rising,
    detect_limit_up_pct, is_limit_up,
)
from .layer_b_launch import LaunchSignal

logger = logging.getLogger(__name__)


@dataclass
class SustainSignal:
    """C 层单只股票的持续性判定"""
    ts_code: str
    eval_date: str
    score: float = 0.0
    factors: Dict[str, float] = field(default_factory=dict)
    details: Dict[str, str] = field(default_factory=dict)
    passed: bool = False
    b_signal: Optional[LaunchSignal] = None


class LayerCSustainAnalyzer:
    """C 层：持续性判定"""

    def __init__(self, cfg: MainUptrendConfig,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg
        self.loader = loader or DataLoader()

    def evaluate(self, ts_code: str, eval_date: str,
                 b_signal: Optional[LaunchSignal] = None) -> SustainSignal:
        sig = SustainSignal(
            ts_code=ts_code, eval_date=eval_date, b_signal=b_signal,
        )

        df = self.loader.get_daily(ts_code, start_date="2020-01-01", end_date=eval_date)
        if df is None or len(df) < 20:
            sig.details["error"] = "日线数据不足"
            return sig

        df = df.reset_index(drop=True)
        if len(df) < 2:
            sig.details["error"] = "至少需要 2 日数据"
            return sig

        last = df.iloc[-1]
        prev = df.iloc[-2]
        today_close = float(last["close"])
        today_volume = float(last["volume"])
        today_amount = float(last["amount"])
        today_pct = float(last["pct_chg"])
        prev_volume = float(prev["volume"])

        scores = {}
        details = {}
        passed_count = 0

        # --------------------------------------------------
        # C1: 分时形态质量
        # --------------------------------------------------
        df_1min = self.loader.get_1min_kline(ts_code, eval_date)
        if df_1min is not None and len(df_1min) >= 30:
            up_ratio = intraday_up_ratio(df_1min)
            morning = intraday_morning_checks(
                df_1min,
                morning_pct=self.cfg.c_intraday_morning_pct,
                morning_amplitude_max=self.cfg.c_intraday_morning_amplitude_max,
            )
            c1_pass = up_ratio > self.cfg.c_intraday_up_ratio_min or morning["passed"]
            scores["intraday"] = max(up_ratio, 1.0 if morning["passed"] else 0)
            details["intraday"] = f"上行占比={up_ratio:.0%}, 午前形态={'强势' if morning['passed'] else '普通'}"
        else:
            c1_pass = False
            scores["intraday"] = 0
            details["intraday"] = "1分钟线不可用"

        if c1_pass:
            passed_count += 1

        # --------------------------------------------------
        # C2: 大单买入占比（简化为成交额放大 + 涨幅配合）
        # --------------------------------------------------
        avg_amount_20 = df["amount"].iloc[-21:-1].mean() if len(df) >= 21 else df["amount"].mean()
        if avg_amount_20 > 0:
            amount_ratio = today_amount / avg_amount_20
            c2_pass = amount_ratio > 2.0 and today_pct > 2.0
            scores["big_order"] = min(1.0, amount_ratio / 5.0)
            details["big_order"] = f"成交额倍数={amount_ratio:.1f}x, 涨幅={today_pct:.1f}%"
        else:
            c2_pass = False
            scores["big_order"] = 0
            details["big_order"] = "数据不足"
        if c2_pass:
            passed_count += 1

        # --------------------------------------------------
        # C3: 缩量上涨
        # --------------------------------------------------
        c3_pass = volume_shrink_check(today_volume, prev_volume, today_pct)
        scores["vol_shrink"] = 1.0 if c3_pass else 0
        ratio_v = today_volume / prev_volume if prev_volume > 0 else 0
        details["vol_shrink"] = f"量比T-1={ratio_v:.1f}x, 涨跌={today_pct:+.1f}%"
        if c3_pass:
            passed_count += 1

        # --------------------------------------------------
        # C4: 板上量比（连板股次日封板质量）
        # --------------------------------------------------
        limit_pct = detect_limit_up_pct(ts_code)
        is_zt = is_limit_up(today_pct, limit_pct)
        if is_zt:
            seal_time = self._estimate_seal_time(ts_code, eval_date)
            open_times = self._estimate_open_times(ts_code, eval_date)
            early = seal_time <= self.cfg.c_seal_early_time
            tight = open_times <= self.cfg.c_seal_max_open_times
            c4_pass = early and tight
            scores["seal_quality"] = (1.0 if early else 0.3) + (1.0 if tight else 0.1)
            details["seal_quality"] = f"封板={seal_time}, 开板={open_times}次"
        else:
            c4_pass = False
            scores["seal_quality"] = 0
            details["seal_quality"] = "非涨停日"
        if c4_pass:
            passed_count += 1

        # --------------------------------------------------
        # C5: 同板块联动
        # --------------------------------------------------
        peer_pct = self._get_sector_peers_pct(ts_code, eval_date)
        sector_result = sector_peers_rising(
            peer_pct,
            sector_rise_min=self.cfg.c_sector_rise_min_pct,
            peer_count_min=self.cfg.c_sector_peer_count_min,
        )
        c5_pass = sector_result["passed"]
        scores["sector"] = 1.0 if c5_pass else 0
        details["sector"] = f"板块均值={sector_result['sector_avg'] * 100:.1f}%, 上涨={sector_result['rising_count']}只"
        if c5_pass:
            passed_count += 1

        sig.factors = scores
        sig.details = details
        sig.score = sum(scores.values())
        sig.passed = passed_count >= 3

        return sig

    def _estimate_seal_time(self, ts_code: str, eval_date: str) -> str:
        """估算封板时间（1分钟线检测首次触板）"""
        df_1min = self.loader.get_1min_kline(ts_code, eval_date)
        if df_1min is None or len(df_1min) < 30:
            return "00:00"
        limit_pct = detect_limit_up_pct(ts_code)
        open_price = float(df_1min["close"].iloc[0])
        limit_price = open_price * (1 + limit_pct) if open_price > 0 else 0
        for _, row in df_1min.iterrows():
            if float(row["high"]) >= limit_price * 0.999:
                return str(row["time"])[:5]
        return "00:00"

    def _estimate_open_times(self, ts_code: str, eval_date: str) -> int:
        """估算开板次数（1分钟线板价上下穿行次数）"""
        df_1min = self.loader.get_1min_kline(ts_code, eval_date)
        if df_1min is None or len(df_1min) < 30:
            return 99
        limit_pct = detect_limit_up_pct(ts_code)
        open_price = float(df_1min["close"].iloc[0])
        limit_price = open_price * (1 + limit_pct) if open_price > 0 else 0
        below_limit = df_1min["high"] < limit_price * 0.999
        open_times = 0
        was_below = True
        for i in range(len(below_limit)):
            if not below_limit.iloc[i] and was_below:
                open_times += 1
            was_below = below_limit.iloc[i]
        return max(0, open_times - 1)

    def _get_sector_peers_pct(self, ts_code: str, eval_date: str) -> List[float]:
        """获取同概念板块其他标的当日涨幅"""
        conn = None
        try:
            import sys, os
            sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
            from core.db.connection import get_db_fresh
            from psycopg2.extras import RealDictCursor

            conn = get_db_fresh()
            cur = conn.cursor(cursor_factory=RealDictCursor)

            cur.execute("""
                SELECT industry FROM daily_quotes
                WHERE ts_code = %s
                  AND trade_date = %s
                  AND industry IS NOT NULL
                LIMIT 1
            """, (ts_code, eval_date))
            row = cur.fetchone()
            if not row or not row['industry']:
                cur.close()
                return []

            industry = row['industry']
            cur.execute("""
                SELECT ts_code, pct_chg FROM daily_quotes
                WHERE trade_date = %s
                  AND industry = %s
                  AND pct_chg IS NOT NULL
                  AND ts_code != %s
            """, (eval_date, industry, ts_code))
            results = [float(r['pct_chg']) for r in cur.fetchall()]
            cur.close()
            return results
        except Exception:
            return []
        finally:
            if conn and not conn.closed:
                conn.close()

    def scan_b_signals(self, b_signals: List[LaunchSignal],
                       top_n: int = 8) -> List[SustainSignal]:
        """
        对 B 层输出的 Top N 信号做持续性二次过滤
        """
        results = []
        for b_sig in b_signals:
            c_sig = self.evaluate(b_sig.ts_code, b_sig.eval_date, b_sig)
            if c_sig.passed:
                results.append(c_sig)

        results.sort(key=lambda x: x.score, reverse=True)
        top = results[:top_n]
        logger.info(f"C 层扫描 {len(b_signals)} 只，通过 {len(results)} 只，输出 Top {len(top)} 只")
        return top