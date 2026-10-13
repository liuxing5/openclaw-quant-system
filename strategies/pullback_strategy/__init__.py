"""
回调买入法 (Pullback Buy Strategy)
====================================
来源: @OtherSideBJ 推文介绍的 Martin Luk (2025 US Investing Championship $20K 组冠军,
年收益 969.8%) 最经典策略 —— 回调买入法。

核心思想: 专门寻找强势股回到支撑附近的机会，等价格重新回升时买入，
让买点尽量靠近止损位置，用较小的试错成本争取强势股下一段上涨。

四步骤 (与推文一致):
  1. 强势股筛选     —— 20日涨幅≥20%，EMA9>EMA21>EMA50 多头排列
  2. 回调到支撑     —— 自20日高点回撤 5%~25%，低点触及 EMA21/EMA50 支撑区且缩量
  3. 价格回升确认   —— 当日收阳、收盘重新站上 EMA21 且高于前一日收盘
  4. 紧止损入场     —— 止损=min(近5日低点, EMA21)下方1%，风险≤3%；目标=入场+2×风险

结果写入 daily_candidates, source='pullback_buy'，
扫描统计写入 strategy_scans, strategy='pullback_buy'。
"""
from .config import PullbackConfig, DEFAULT_CONFIG

__version__ = "0.1.0"
