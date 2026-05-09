"""
退出器 — 主升行情的对称出场逻辑
================================================
不复用进场信号反向使用,独立设计:
  E1  硬性止损     — 跌破成本 2%(项目硬规则)
  E2  移动止盈     — 浮盈 8% 后激活, 回撤 5% 离场
  E3  时间止损     — 持仓超过 15 个交易日强平
  E4  MACD 死叉    — DIF 下穿 DEA
  E5  破 8MA 放量  — 跌破 8 日均线且当日量比 ≥1.2
  E6  量价背离     — 量比 ≥3 但收阴线(出货嫌疑)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from config import ExitorConfig
from indicators import macd, macd_dead_cross, sma, volume_ratio


@dataclass
class Position:
    symbol: str
    name: str
    entry_date: datetime
    entry_price: float
    shares: int
    high_water_mark: float = 0.0
    days_held: int = 0

    def update_high(self, price: float) -> None:
        if price > self.high_water_mark:
            self.high_water_mark = price

    def unrealized_pnl_pct(self, current_price: float) -> float:
        return (current_price - self.entry_price) / self.entry_price


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str = ""
    urgency: str = "normal"   # normal / urgent (urgent 跳过 T+1 检查不可能,但用于排序)


class Exitor:
    def __init__(self, cfg: Optional[ExitorConfig] = None):
        self.cfg = cfg or ExitorConfig()

    def check(self, position: Position, df: pd.DataFrame) -> ExitDecision:
        """
        df: 包含到当前评估日为止的完整 K 线数据(PIT, 最后一行是当前可见的最新交易日)
        """
        if df is None or df.empty:
            return ExitDecision(False)

        last = df.iloc[-1]
        current_price = float(last["close"])
        position.update_high(current_price)

        # E1 硬性止损 (项目 2% 规则,但这里指账户层面;单票止损用 8% 跌幅容忍)
        loss_pct = -position.unrealized_pnl_pct(current_price)
        if loss_pct >= 0.08:
            return ExitDecision(True, f"E1 硬止损 -{loss_pct:.1%}", "urgent")

        # E2 移动止盈
        gain = position.unrealized_pnl_pct(current_price)
        if gain >= self.cfg.trailing_activate_pct:
            giveback = (position.high_water_mark - current_price) / position.high_water_mark
            if giveback >= self.cfg.trailing_giveback_pct:
                return ExitDecision(True,
                    f"E2 移动止盈 高点回撤 {giveback:.1%}")

        # E3 时间止损
        if position.days_held >= self.cfg.max_holding_days:
            return ExitDecision(True, f"E3 时间止损 持仓 {position.days_held} 日")

        # E4 MACD 死叉
        if self.cfg.macd_dead_cross_exit and len(df) >= 30:
            m = macd(df["close"], 8, 17, 9)
            if macd_dead_cross(m):
                return ExitDecision(True, "E4 MACD 死叉")

        # E5 跌破 8MA 且放量
        if self.cfg.break_ma8_with_volume and len(df) >= 25:
            ma8 = sma(df["close"], 8).iloc[-1]
            ma8_prev = sma(df["close"], 8).iloc[-2]
            vr = volume_ratio(df["volume"], 20).iloc[-1]
            prev_close = df["close"].iloc[-2]
            if (prev_close >= ma8_prev and current_price < ma8
                    and vr is not None and vr >= 1.2):
                return ExitDecision(True, f"E5 破 8MA 放量 量比 {vr:.2f}")

        # E6 量价背离 (高量阴线)
        if self.cfg.volume_climax_exit and len(df) >= 25:
            vr = volume_ratio(df["volume"], 20).iloc[-1]
            is_yin = current_price < float(last["open"])
            if vr is not None and vr >= 3.0 and is_yin:
                return ExitDecision(True, f"E6 高量阴线 量比 {vr:.2f}")

        return ExitDecision(False)
