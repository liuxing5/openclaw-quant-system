"""
主升浪检测策略 (Main Uptrend Detection)
========================================
四层漏斗架构，从基本面预筛到风险过滤，提前发现具备持续上涨潜力的标的。

四层架构：
  Layer A - 选股池预筛（周频）：业绩加速 + 市值适中 + 行业景气 + 股权激励
  Layer B - 启动信号识别（日频）：量能突破 + 价格突破 + 主力资金 + 封单质量 + 次日强度
  Layer C - 持续性判定（日频）：分时形态 + 大单买入 + 缩量上涨 + 板上量比 + 板块联动
  Layer D - 风险过滤：ST/减持/诱多涨停/高质押

与现有策略关系：
  - 与 overnight_8step / funnel_strategy / llm_multisource 并列
  - 侧重"持续性"标的，非单日涨停回落
  - 结果写入 daily_candidates，source='main_uptrend'
"""
from .config import MainUptrendConfig, DEFAULT_CONFIG
from .engine import MainUptrendEngine

__version__ = "0.1.0"