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

    a_profit_growth_min: float = 0.20
    a_profit_acceleration: bool = True

    a_market_cap_min: float = 30e8
    a_market_cap_max: float = 500e8

    a_industry_momentum_top_pct: float = 0.50
    a_industry_momentum_days: int = 20

    a_incentive_lookback_months: int = 6

    a_use_union: bool = True

    # ============================================================
    # Layer B: 启动信号识别（日频）
    # ============================================================
    b_enabled: bool = True

    b_volume_breakout_mult: float = 2.5
    b_volume_ma_days: int = 60
    b_turnover_min: float = 5.0

    b_price_breakout_box_days: int = 60
    b_price_ma_period: int = 120
    b_price_above_ma_max_pct: float = 0.10

    b_main_force_inflow_min_pct: float = 0.03

    b_seal_amount_ratio_min: float = 0.005

    b_next_day_hold_avg_price: bool = True

    # ============================================================
    # Layer C: 持续性判定（日频）
    # ============================================================
    c_enabled: bool = True

    c_intraday_morning_pct: float = 0.03
    c_intraday_morning_amplitude_max: float = 0.02
    c_intraday_up_ratio_min: float = 0.60

    c_big_order_net_buy_min_pct: float = 0.05
    c_big_order_threshold: float = 500000

    c_volume_shrink_ratio_min: float = 0.35
    c_volume_shrink_ratio_max: float = 0.80

    c_seal_early_time: str = "10:00"
    c_seal_max_open_times: int = 0

    c_sector_rise_min_pct: float = 0.02
    c_sector_peer_count_min: int = 1

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

    d_max_gain_20d: float = 0.80
    d_near_high_pct: float = 1.0
    d_max_drop_5d: float = 0.20
    d_max_turnover: float = 45.0

    # ============================================================
    # Layer E: 趋势持续型检测（日频）
    # ============================================================
    e_enabled: bool = True

    e_ma_alignment_days: int = 20
    e_price_above_short_ma_pct: float = 0.02
    e_max_drawdown_20d: float = 0.12

    e_volume_staircase_min: float = 1.1
    e_volume_staircase_periods: int = 5

    e_momentum_positive_min: int = 3
    e_momentum_slope_stable: bool = True

    e_adx_threshold: float = 25.0
    e_rsi_range_min: float = 45.0
    e_rsi_range_max: float = 80.0

    e_trend_duration_min: int = 10
    e_top_n_daily: int = 20

    # ============================================================
    # LLM 优选模块
    # ============================================================
    llm_enabled: bool = False
    llm_model: str = "gpt-4o-mini"
    llm_max_candidates: int = 10
    llm_final_top_n: int = 5
    b_top_n_daily: int = 50  # 放宽：5→50，允许更多启动信号进入C层
    c_top_n_daily: int = 5  # 收紧：8→5

    backtest_start: str = "2025-01-01"
    backtest_end: str = "2026-05-15"

    forward_return_days: List[int] = field(default_factory=lambda: [10, 20, 60])

    db_batch_size: int = 1000


DEFAULT_CONFIG = MainUptrendConfig()