"""
Layer B: 启动信号识别（日频）
===============================
"启动度"复合因子，每日盘后扫描 A 层预筛池：
  1. 量能突破：成交额 > 60日均值的 2.5 倍，换手率 > 5%
  2. 价格突破：突破前 60 日箱体上沿，或突破半年线且距半年线涨幅 < 8%
  3. 主力资金净流入：当日主力净流入 > 总成交额的 5%
  4. 封单质量（涨停日）：封单金额 / 流通市值 > 0.5%
  5. 次日强度：次日不破当日均价，缩量整理或继续放量上攻
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set

import pandas as pd
import numpy as np

from .config import MainUptrendConfig
from .data_loader import DataLoader
from .indicators import (
    sma, volume_ma, box_range, near_ma,
    detect_limit_up_pct, is_limit_up,
)

logger = logging.getLogger(__name__)


@dataclass
class LaunchSignal:
    """B 层单只股票的启动信号"""
    ts_code: str
    eval_date: str
    score: float = 0.0
    factors: Dict[str, float] = field(default_factory=dict)
    details: Dict[str, str] = field(default_factory=dict)
    passed: bool = False


class LayerBLaunchDetector:
    """B 层：启动信号识别"""

    def __init__(self, cfg: MainUptrendConfig,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg
        self.loader = loader or DataLoader()

    def evaluate(self, ts_code: str, eval_date: str) -> LaunchSignal:
        """
        评估单只股票在 eval_date 的启动信号

        返回 LaunchSignal，含 composite score
        """
        sig = LaunchSignal(ts_code=ts_code, eval_date=eval_date)

        df = self.loader.get_daily(ts_code, start_date="2020-01-01", end_date=eval_date, min_days=self.cfg.b_volume_ma_days + 10)
        if df is None or len(df) < self.cfg.b_volume_ma_days + 10:
            sig.details["error"] = "数据不足"
            return sig

        df = df.reset_index(drop=True)
        last = df.iloc[-1]
        today_close = float(last["close"])
        today_volume = float(last["volume"])
        today_amount = float(last["amount"])
        today_pct = float(last["pct_chg"])
        today_turn = float(last.get("turnover_rate", 0))

        scores = {}
        details = {}

        # --------------------------------------------------
        # B1: 量能突破
        # --------------------------------------------------
        vol_ma = volume_ma(df["amount"], self.cfg.b_volume_ma_days).iloc[-1]
        if pd.notna(vol_ma) and vol_ma > 0:
            vol_breakout = today_amount / vol_ma
            b1_pass = vol_breakout > self.cfg.b_volume_breakout_mult and today_turn > self.cfg.b_turnover_min
            scores["vol_breakout"] = min(1.0, vol_breakout / (self.cfg.b_volume_breakout_mult * 2))
            details["vol_breakout"] = f"量比MA60={vol_breakout:.1f}x, 换手={today_turn:.1f}%"
        else:
            b1_pass = False
            scores["vol_breakout"] = 0
            details["vol_breakout"] = "量比MA60数据不足"

        # --------------------------------------------------
        # B2: 价格突破
        # --------------------------------------------------
        box = box_range(df["high"], df["low"], self.cfg.b_price_breakout_box_days)
        box_high_val = box["box_high"].iloc[-2] if len(box) > 1 else 0
        box_low_val = box["box_low"].iloc[-2] if len(box) > 1 else 0
        ma_half_year = sma(df["close"], self.cfg.b_price_ma_period)
        ma_half_val = ma_half_year.iloc[-1] if len(ma_half_year) > 0 and pd.notna(ma_half_year.iloc[-1]) else 0

        breakout_box = today_close > box_high_val if box_high_val > 0 else False
        breakout_ma = near_ma(
            pd.Series([today_close]),
            pd.Series([ma_half_val]),
            self.cfg.b_price_above_ma_max_pct
        ).iloc[0] if ma_half_val > 0 else False

        b2_pass = breakout_box or breakout_ma
        if breakout_box:
            scores["price_breakout"] = 0.8
            details["price_breakout"] = f"突破{self.cfg.b_price_breakout_box_days}日箱体上沿"
        elif breakout_ma:
            scores["price_breakout"] = 0.6
            details["price_breakout"] = f"突破半年线(距MA={((today_close - ma_half_val) / ma_half_val * 100):.1f}%)"
        else:
            scores["price_breakout"] = 0
            details["price_breakout"] = "未突破"

        # --------------------------------------------------
        # B3: 主力资金净流入
        # --------------------------------------------------
        flow_df = self.loader.get_main_force_flow(ts_code,
                                                   start_date="2025-01-01",
                                                   end_date=eval_date)
        if flow_df is not None and len(flow_df) > 0:
            today_flow = flow_df[flow_df["trade_date"].astype(str) == eval_date]
            if len(today_flow) > 0:
                net_inflow = float(today_flow["main_net_inflow"].iloc[0] or 0)
                inflow_pct = abs(net_inflow) / today_amount if today_amount > 0 else 0
                b3_pass = inflow_pct > self.cfg.b_main_force_inflow_min_pct and net_inflow > 0
                scores["main_force"] = min(1.0, inflow_pct / (self.cfg.b_main_force_inflow_min_pct * 5))
                details["main_force"] = f"主力净流入={net_inflow / 1e4:.0f}万({inflow_pct * 100:.1f}%)"
            else:
                b3_pass = False
                scores["main_force"] = 0
                details["main_force"] = "当日无主力资金数据"
        else:
            b3_pass = False
            scores["main_force"] = 0
            details["main_force"] = "主力资金数据不可用"

        # --------------------------------------------------
        # B4: 封单质量（涨停日）
        # --------------------------------------------------
        limit_pct = detect_limit_up_pct(ts_code)
        is_zt = is_limit_up(today_pct, limit_pct)
        if is_zt:
            seal_score = self._estimate_seal_quality(ts_code, eval_date, today_amount)
            b4_pass = seal_score > self.cfg.b_seal_amount_ratio_min
            scores["seal_quality"] = min(1.0, seal_score / (self.cfg.b_seal_amount_ratio_min * 2))
            details["seal_quality"] = f"涨停封单比={seal_score * 100:.2f}%"
        else:
            b4_pass = True
            scores["seal_quality"] = 0.3
            details["seal_quality"] = "非涨停日"

        b1_b4 = [
            scores.get("vol_breakout", 0) > 0,
            scores.get("price_breakout", 0) > 0,
            scores.get("main_force", 0) > 0,
            scores.get("seal_quality", 0) > 0,
        ]
        passed_b1_b4 = sum(b1_b4) >= 3

        # --------------------------------------------------
        # B5: 次日强度（需要 T+1 数据验证，此处返回到外部判断）
        # --------------------------------------------------
        sig.factors = scores
        sig.details = details
        sig.score = sum(scores.values())
        sig.passed = passed_b1_b4

        return sig

    def _estimate_seal_quality(self, ts_code: str, eval_date: str,
                                today_amount: float) -> float:
        """估算封单质量（简化版，无 L2 数据时按历史规律估算）"""
        try:
            df = self.loader.get_daily(ts_code, start_date="2025-01-01", end_date=eval_date)
            if df is None or len(df) < 60:
                return 0
            avg_amount = df["amount"].iloc[-60:].mean()
            if avg_amount > 0:
                return today_amount / avg_amount * 0.001
        except Exception:
            pass
        return 0

    def scan_pool(self, pool: Set[str], eval_date: str,
                  top_n: int = 20) -> List[LaunchSignal]:
        """
        扫描 A 层预筛池，返回 Top N 启动信号

        优化：使用全市场快照批量计算，避免逐只股票查DB
        """
        # ---- 快速路径：用快照批量预筛 ----
        snapshot = self.loader.get_market_snapshot(eval_date, min_amount=0)
        if snapshot.empty:
            logger.info(f"B 层扫描 {len(pool)} 只，快照为空，跳过")
            return []

        # 构建快照索引
        snap_map: Dict[str, dict] = {}
        for _, row in snapshot.iterrows():
            snap_map[row['ts_code']] = row.to_dict()

        # 批量计算量能指标（用快照数据）
        # 先收集需要详细评估的候选
        quick_candidates = []
        for code in pool:
            if code not in snap_map:
                continue
            row = snap_map[code]
            today_amount = float(row.get('amount', 0))
            today_turn = float(row.get('turnover_rate', 0))
            today_pct = float(row.get('pct_chg', 0))

            # 快速预筛：至少满足量能或涨幅门槛之一
            if today_amount < 1e8 and abs(today_pct) < 3:
                continue
            quick_candidates.append(code)

        logger.info(f"B 层扫描 {len(pool)} 只，快照命中 {len(quick_candidates)} 只，进入详细评估")

        # ---- 详细评估（只对预筛通过的） ----
        results = []
        for code in quick_candidates:
            sig = self.evaluate(code, eval_date)
            if sig.passed:
                results.append(sig)

        results.sort(key=lambda x: x.score, reverse=True)
        top = results[:top_n]
        logger.info(f"B 层扫描 {len(pool)} 只，通过 {len(results)} 只，输出 Top {len(top)} 只")
        return top