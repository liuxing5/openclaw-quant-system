"""回调买入法 — 参数配置"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PullbackConfig:
    # ---- 步骤1: 强势股筛选 ----
    momentum_lookback: int = 20       # 动能回看窗口(交易日)
    min_momentum_pct: float = 20.0    # 20日累计涨幅 ≥ 20%
    ema_fast: int = 9
    ema_mid: int = 21
    ema_slow: int = 50

    # ---- 步骤2: 回调到支撑 ----
    pullback_lookback: int = 20       # 20日高点窗口
    min_pullback_pct: float = 5.0     # 自高点回撤 ≥ 5%
    max_pullback_pct: float = 25.0    # 但不超过 25%（浅回调，趋势未坏）
    support_tolerance_pct: float = 3.0   # 当日最低价进入 EMA21/EMA50 上下 3% 视为"触及支撑"
    volume_shrink_ratio: float = 0.9  # 回调日量能相对20日均量 ≤ 90%（缩量回调）

    # ---- 步骤3: 价格回升确认 ----
    require_reclaim_ema21: bool = True   # 收盘重新站上 EMA21
    require_bullish_day: bool = True     # 当日收阳 (close > open)
    require_beat_prev_close: bool = True # 收盘高于前一日收盘

    # ---- 步骤4: 紧止损入场 ----
    stop_lookback: int = 5            # 近5日低点
    stop_below_support_pct: float = 1.0  # 止损设在支撑下方 1%
    max_risk_pct: float = 3.0         # 入场到止损的风险 ≤ 3%
    reward_risk: float = 2.0          # 目标 = 入场 + 2×风险

    # ---- 流动性/风控过滤 ----
    min_amount_yi: float = 0.5        # 成交额 ≥ 0.5亿
    min_close: float = 3.0            # 股价 ≥ 3元
    exclude_st: bool = True
    exclude_new_days: int = 60        # 剔除上市不足60天的次新股
    exclude_limit_day: bool = True    # 剔除当日涨停/跌停（无法成交）

    # ---- 每日输出 ----
    top_n: int = 10                   # 每日输出候选数量

    # ---- 回测 ----
    bt_start: str = "2025-01-01"
    bt_end: str = ""                  # 空 = 最新日期
    bt_max_hold: int = 10             # 最长持有交易日
    bt_universe_top_momentum: int = 0 # 0 = 全市场，否则只取每日动能前N（加速回测）

    # ---- 批量 ----
    batch_limit: int = 400            # 每批处理股票数（控制内存）


DEFAULT_CONFIG = PullbackConfig()
