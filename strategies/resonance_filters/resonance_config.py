"""
5策略共振配置
==========================
核心策略（必选）：
  1. 20周线保命法（收盘价站上20周均线）
  2. 均线多头排列（5日>10日>20日）
  3. MACD金叉（零轴上方，红柱放大）

增强策略（强烈推荐）：
  4. 布林上轨追涨（刚突破上轨且量比放大）
  5. 年线定海神针（收盘价站上250日均线）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class ResonanceConfig:
    """5策略共振配置"""

    # 核心策略开关（必选）
    enable_ma_20week: bool = True
    enable_ma_bullish: bool = True
    enable_macd: bool = True

    # 增强策略开关（可选）
    enable_bollinger: bool = True
    enable_annual_line: bool = True

    # 过滤参数
    min_pass_count: int = 3  # 最少通过的策略数量
    require_core: bool = True  # 是否要求核心3策略必须全部通过

    # 20周线参数
    ma_20week_period: int = 100  # 20周约100个交易日
    ma_20week_trend_check_days: int = 5  # 趋势判断天数
    ma_20week_min_bias: float = 0.0  # 最小偏离度（%）

    # 均线多头参数
    ma_bullish_periods: List[int] = field(default_factory=lambda: [5, 10, 20])
    ma_bullish_require_above_all: bool = True  # 股价必须在所有均线上方
    ma_bullish_close_tolerance: float = 0.005  # 收盘价低于MA5的容差（0.5%），允许回踩不破

    # MACD参数
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_require_above_zero: bool = True  # 必须在零轴上方（DIF > 0.01）
    macd_require_golden_cross: bool = False  # 必须金叉（False表示金叉或红柱放大均可）
    macd_require_expanding: bool = True  # 红柱必须放大（允许10%小幅缩短）

    # 布林带参数
    bollinger_period: int = 20
    bollinger_std_dev: int = 2
    bollinger_min_volume_ratio: float = 1.5  # 最小量比
    bollinger_require_breakout: bool = True  # 必须突破上轨
    bollinger_exclude_pullback: bool = True  # 排除回踩

    # 年线参数
    annual_line_period: int = 250
    annual_line_min_bias: float = 0.0  # 最小偏离度（%）

    # LLM多源参数
    llm_min_score: float = 25.0  # LLM最低分数阈值
    llm_require_intersection: bool = False  # 是否要求与共振结果取交集

    # 八步法参数
    overnight_score_threshold: int = 80  # 八步法最低评分
    overnight_min_amount: float = 1e8  # 最小成交额
    overnight_max_amount: float = 5e9  # 最大成交额
    overnight_vol_ratio_min: float = 1.5  # 最小量比
    overnight_vol_ratio_max: float = 10.0  # 最大量比
    overnight_turn_min: float = 3.0  # 最小换手率
    overnight_turn_max: float = 20.0  # 最大换手率

    @property
    def total_strategies(self) -> int:
        """总策略数量"""
        count = 3  # 核心3策略
        if self.enable_bollinger:
            count += 1
        if self.enable_annual_line:
            count += 1
        return count

    @property
    def core_strategies(self) -> List[str]:
        """核心策略列表"""
        return ['ma_20week', 'ma_bullish', 'macd']

    @property
    def all_enabled_strategies(self) -> List[str]:
        """所有启用的策略列表"""
        strategies = self.core_strategies.copy()
        if self.enable_bollinger:
            strategies.append('bollinger')
        if self.enable_annual_line:
            strategies.append('annual_line')
        return strategies

    def validate(self) -> List[str]:
        """验证配置，返回错误列表"""
        errors = []

        if self.min_pass_count < 1:
            errors.append("min_pass_count 必须 >= 1")

        if self.min_pass_count > self.total_strategies:
            errors.append(f"min_pass_count 不能大于总策略数 {self.total_strategies}")

        if self.require_core and self.min_pass_count < 3:
            errors.append("require_core=True 时，min_pass_count 必须 >= 3")

        if self.macd_fast >= self.macd_slow:
            errors.append("macd_fast 必须 < macd_slow")

        if self.bollinger_period < 10:
            errors.append("bollinger_period 必须 >= 10")

        return errors


# 默认配置
DEFAULT_CONFIG = ResonanceConfig()

# 保守配置（更严格的过滤）
CONSERVATIVE_CONFIG = ResonanceConfig(
    min_pass_count=5,
    require_core=True,
    enable_bollinger=True,
    enable_annual_line=True,
    llm_min_score=30.0,
    overnight_score_threshold=85,
)

# 激进配置（更宽松的过滤）
AGGRESSIVE_CONFIG = ResonanceConfig(
    min_pass_count=3,
    require_core=False,
    enable_bollinger=True,
    enable_annual_line=False,
    llm_min_score=20.0,
    overnight_score_threshold=75,
)
