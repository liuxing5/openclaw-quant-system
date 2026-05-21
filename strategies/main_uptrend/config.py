"""
主升浪策略配置
==================
Layer A/B/C/D 四层参数，支持回测和实盘两种模式。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo
from typing import List, Optional

TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class MainUptrendConfig:
    # ============================================================
    # Layer A: 选股池预筛（周频）
    # ============================================================
    a_enabled: bool = True

    a_profit_growth_min: float = 0.30
    a_profit_acceleration: bool = True

    a_market_cap_min: float = 50e8
    a_market_cap_max: float = 200e8

    a_industry_momentum_top_pct: float = 0.30
    a_industry_momentum_days: int = 20

    a_incentive_lookback_months: int = 6

    # ============================================================
    # Layer B: 启动信号识别（日频）
    # ============================================================
    b_enabled: bool = True

    b_volume_breakout_mult: float = 3.5  # 提高：3.0→3.5，要求更明显的放量
    b_volume_ma_days: int = 60
    b_turnover_min: float = 10.0  # 提高：8→10，要求更高活跃度

    b_price_breakout_box_days: int = 60
    b_price_ma_period: int = 120
    b_price_above_ma_max_pct: float = 0.05  # 收紧：0.08→0.05，要求更接近突破点

    b_main_force_inflow_min_pct: float = 0.08  # 提高：0.05→0.08，要求更多主力流入

    b_seal_amount_ratio_min: float = 0.005

    b_next_day_hold_avg_price: bool = True

    # ============================================================
    # Layer C: 持续性判定（日频）
    # ============================================================
    c_enabled: bool = True

    c_intraday_morning_pct: float = 0.03
    c_intraday_morning_amplitude_max: float = 0.02
    c_intraday_up_ratio_min: float = 0.60

    c_big_order_net_buy_min_pct: float = 0.10  # 提高：0.08→0.10
    c_big_order_threshold: float = 500000

    c_volume_shrink_ratio_min: float = 0.45  # 收紧：0.50→0.45，要求更明显的缩量
    c_volume_shrink_ratio_max: float = 0.65  # 收紧：0.70→0.65

    c_seal_early_time: str = "10:00"
    c_seal_max_open_times: int = 0

    c_sector_rise_min_pct: float = 0.05  # 提高：0.03→0.05，要求行业更强
    c_sector_peer_count_min: int = 3  # 提高：2→3，要求更多同板块联动

    # ============================================================
    # Layer D: 风险过滤
    # ============================================================
    d_enabled: bool = True

    d_exclude_st: bool = True
    d_exclude_delist_warning: bool = True

    d_share_reduction_days: int = 30

    d_trap_volume_ratio: float = 5.0
    d_trap_seal_ratio_max: float = 0.003

    d_pledge_ratio_max: float = 0.50
    d_pledge_consecutive_limit_days: int = 3

    d_max_gain_20d: float = 0.30  # 收紧：50%→30%，20日涨幅超过30%的剔除（追高风险）
    d_near_high_pct: float = 0.95  # 新增：收盘价接近52周高点95%以上剔除（高位接盘风险）
    d_max_drop_5d: float = 0.15  # 新增：近5日跌幅超过15%剔除（接飞刀风险）
    d_max_turnover: float = 25.0  # 新增：换手率超过25%剔除（高位派发风险）

    # ============================================================
    # 综合参数
    # ============================================================
    b_top_n_daily: int = 5  # 收紧：20→5，只让最强的5个信号进入C层
    c_top_n_daily: int = 5  # 收紧：8→5

    backtest_start: str = "2025-01-01"
    backtest_end: str = "2026-05-15"

    forward_return_days: List[int] = field(default_factory=lambda: [10, 20, 60])

    db_batch_size: int = 1000


DEFAULT_CONFIG = MainUptrendConfig()