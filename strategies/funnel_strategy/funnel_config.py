"""
漏斗策略配置 — 七步闭环全部参数
===================================
吸收策略来源标注: [①基本面] [②均线/趋势] [③八步法] [④纪律] [⑤VWAP/价格行为]
                  [⑥人气榜] [⑦海龟风控]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class FunnelConfig:
    """七步漏斗策略完整配置"""

    # ================================================================
    # Layer 0: 大盘风控（盘前）[③看大盘控仓位]
    # ================================================================
    layer0_enabled: bool = True
    layer0_min_advancers: int = 2500          # 两市上涨≥此数才荐股
    layer0_index_code: str = '000001.SH'       # 全A指数（上证综指为参考）
    layer0_index_ema_period: int = 20           # 指数20EMA
    layer0_partial_cap: float = 0.50            # 未通过时仓位≤50%

    # ================================================================
    # Layer 1: 硬性防雷 [①巴菲特准则/基本面]
    # ================================================================
    layer1_enabled: bool = True
    layer1_exclude_st: bool = True              # 剔除ST [③八步法]
    layer1_exclude_new_ipo_days: int = 60       # 次新股过滤 [③八步法]
    layer1_min_current_ratio: float = 1.2       # 流动比率>1.2 [①巴菲特]
    layer1_max_debt_ratio: float = 65.0          # 负债率<65% [①巴菲特]
    layer1_revenue_check_quarters: int = 3       # 近N季度 [①基本面]
    layer1_max_consecutive_rev_decline: int = 0  # 营收连续负增≤0次 [①基本面]
    layer1_min_revenue_yoy: float = -10.0        # 营收同比最低（%） [①基本面]

    # ================================================================
    # Layer 2: 流动性筛选 [③八步法/⑥人气榜]
    # ================================================================
    layer2_enabled: bool = True
    layer2_min_avg_amount_20d: float = 1e8       # 20日日均成交额>1亿 [③八步法]
    layer2_min_circulating_mcap: float = 2e9     # 流通市值>20亿 [③八步法]
    layer2_turn_rate_min: float = 3.0             # 换手率≥3% [③八步法]
    layer2_turn_rate_max: float = 15.0            # 换手率≤15% [③八步法]

    # ================================================================
    # Layer 3: 趋势结构过滤 [②20周保命法/均线多头/年线/右侧交易]
    # ================================================================
    layer3_enabled: bool = True
    layer3_weekly_ma_period: int = 20             # 周线MA(约100日) [②20周保命法]
    layer3_ema_fast: int = 12                     # EMA快线 [②均线多头]
    layer3_ema_mid: int = 26                      # EMA中线
    layer3_ema_slow: int = 50                     # EMA慢线
    layer3_annual_ma_period: int = 250            # 年线(200EMA) [②年线定海神针]
    layer3_require_above_ema12: bool = True       # 股价在EMA12上方 [②右侧交易]
    layer3_bonus_above_annual: float = 3.0        # 股价>年线加分 [②年线定海神针]
    layer3_trend_structure_modes: List[str] = field(
        default_factory=lambda: ['ascending_platform', 'pullback_support']
    )  # 上升平台 / 回踩支撑 [②右侧交易]

    # ================================================================
    # Layer 4: 动能与买入信号 [⑤价格行为/VWAP/一进二改良/布林反用]
    # ================================================================
    layer4_enabled: bool = True
    # K线形态识别 [⑤价格行为]
    layer4_enable_demand_absorption: bool = True   # 需求吸收K线
    layer4_enable_strong_relay: bool = True         # 强势接力（一进二改良）
    layer4_volume_ratio_min: float = 1.5            # 最小量比 [③八步法]
    layer4_volume_ratio_max: float = 3.0            # 最大量比
    layer4_max_bias_pct: float = 6.0                # 最大乖离率% [③八步法]
    layer4_vwap_tolerance: float = 0.01             # VWAP翘头容差 [⑤VWAP]
    layer4_require_no_upper_boll_blowout: bool = True  # 无天量上轨 [⑤布林反用]
    layer4_boll_blowout_vol_mult: float = 3.0       # 天量上轨量能倍数

    # ================================================================
    # Layer 5: 人气精选 [③隔夜八步法/⑥人气榜]
    # ================================================================
    layer5_enabled: bool = True
    layer5_min_composite_score: int = 80            # 综合评分≥80 [③八步法]
    layer5_pct_range_low: float = 3.0               # 涨幅下限 [③八步法]
    layer5_pct_range_high: float = 5.0              # 涨幅上限 [③八步法]
    layer5_bonus_popularity_rank: float = 5.0       # 人气榜≤100加分 [⑥人气榜]
    layer5_popularity_rank_threshold: int = 100      # 人气榜排名阈值

    # ================================================================
    # Layer 6: 刚性风控 [⑦海龟风控/ATR]
    # ================================================================
    layer6_enabled: bool = True
    layer6_entry_after_time: str = '14:30'          # 买入时段 [③八步法]
    layer6_atr_period: int = 20                     # ATR计算周期 [⑦海龟]
    layer6_initial_stop_atr: float = 1.0            # 初始止损=入场价-1ATR [⑦海龟]
    layer6_trailing_ref: str = 'ema12'              # 移动止盈参考 (ema12/vwap) [②均线]
    layer6_min_profit_loss_ratio: float = 2.0       # 盈亏比≥2:1 [⑦海龟]
    layer6_target_atr_mult: float = 2.0             # 盈利目标=入场+2ATR

    # ================================================================
    # 核心纪律 [④严格执行纪律/复盘强化规则]
    # ================================================================
    discipline_enable_review: bool = True            # 每晚复盘
    discipline_max_consecutive_fails: int = 3        # 连续止损失败暂停交易 [④纪律]
    discipline_review_check_all: bool = True         # 任一步不满足推倒重来 [④纪律]

    # ================================================================
    # 输出控制
    # ================================================================
    max_final_candidates: int = 5                    # 最终推荐最多5只
    output_dir: str = './results'                    # 输出目录
    verbose: bool = True                             # 详细日志

    @property
    def total_layers(self) -> int:
        return 7  # Layer 0-6

    @property
    def enabled_layers(self) -> List[str]:
        layers = []
        for i in range(7):
            if getattr(self, f'layer{i}_enabled', True):
                layers.append(str(i))
        return layers

    def validate(self) -> List[str]:
        errors = []
        if self.layer0_min_advancers < 0:
            errors.append("layer0_min_advancers 必须 >= 0")
        if self.layer1_min_current_ratio <= 0:
            errors.append("layer1_min_current_ratio 必须 > 0")
        if self.layer2_min_avg_amount_20d <= 0:
            errors.append("layer2_min_avg_amount_20d 必须 > 0")
        if self.layer3_ema_fast >= self.layer3_ema_mid or self.layer3_ema_mid >= self.layer3_ema_slow:
            errors.append("EMA 参数必须满足: fast < mid < slow")
        if self.layer4_volume_ratio_min >= self.layer4_volume_ratio_max:
            errors.append("layer4_volume_ratio_min 必须 < layer4_volume_ratio_max")
        if self.layer6_atr_period < 5:
            errors.append("layer6_atr_period 必须 >= 5")
        if self.layer6_min_profit_loss_ratio < 1.0:
            errors.append("盈亏比 必须 >= 1.0")
        return errors


DEFAULT_FUNNEL_CONFIG = FunnelConfig()
