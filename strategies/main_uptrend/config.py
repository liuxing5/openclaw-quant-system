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

    # 行业动量排名分位数口径（取前 50% 行业），保持分位数形式不动
    a_industry_momentum_top_pct: float = 0.50
    # 行业动量形成期。2026-10-13 参数审查：20 日形成期落在 A 股反转区间
    # （A股证据：超短期动量有效、随后转入反转，只有中期约 60-120 日形成期才有动量证据），
    # 故 20 -> 60 加长。
    # 注意：代码实现用 timedelta(days=...)，单位是自然日（60 自然日 ≈ 40 交易日）；
    # 若按证据区间上沿（4-6 个月）取值需 ~150 自然日，留待主会话回测对比后决定。
    a_industry_momentum_days: int = 60

    a_incentive_lookback_months: int = 6

    a_use_union: bool = True

    # ============================================================
    # Layer B: 启动信号识别（日频）
    # ============================================================
    b_enabled: bool = True

    b_volume_breakout_mult: float = 2.5
    b_volume_ma_days: int = 60
    # 换手率门槛（绝对值，%）。2026-10-13 参数审查已知问题：绝对阈值不随市场状态变化。
    # 向量化扫描路径（layer_b_launch._scan_pool_vectorized）已改为分位数口径：
    #   当日全市场换手率 >= 截面 P30 且 >= 2%（双重条件，防低流动性）；
    # 本字段仅作为降级路径（单票 evaluate，无当日全市场截面）的回退阈值。
    b_turnover_min: float = 5.0

    b_price_breakout_box_days: int = 60
    b_price_ma_period: int = 120
    b_price_above_ma_max_pct: float = 0.10

    # 主力净流入占成交额阈值。2026-10-13 参数审查：主力净流入在控制当日涨跌幅后
    # 预测力≈0，已从硬条件降级为弱加权评分（不参与 passed 判定，评分打 5 折），
    # 本阈值仅用于评分归一化，不再一票否决。
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

    # 大单净买占成交额阈值。2026-10-13 参数审查：订单分类类指标（主力/大单）同属
    # 伪指标，降级为弱加权，不再一票否决。当前代码中没有任何硬条件引用本字段
    # （C 层实际用的是"成交额/20日均额"放量倍数，见 layer_c_sustain C2），
    # 字段保留仅作兼容与后续弱评分归一化用。
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
    e_top_n_daily: int = 5
    e_min_total_score: float = 4.0  # E层总信号硬门槛
    e_top_percentile: float = 66.7  # 只保留综合分前1/3

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