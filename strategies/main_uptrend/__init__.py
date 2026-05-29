"""
主升浪检测策略 (Main Uptrend Detection)
========================================
五层漏斗架构 + LLM 优选，从基本面预筛到风险过滤，提前发现具备持续上涨潜力的标的。

架构：
  Layer A - 选股池预筛（周频）：业绩加速 + 市值适中 + 行业景气 + 股权激励
  Layer B - 启动信号识别（日频）：量能突破 + 价格突破 + 主力资金 + 封单质量 + 次日强度
  Layer C - 持续性判定（日频）：分时形态 + 大单买入 + 缩量上涨 + 板上量比 + 板块联动
  Layer D - 风险过滤：ST/减持/诱多涨停/高质押
  Layer E - 趋势持续型检测（日频）：均线多头 + 阶梯放量 + 动量一致 + ADX/RSI

双通道设计：
  B层(启动型) ─┬─→ C层(持续性) ─┐→ D层(风险过滤) → 输出
  E层(趋势型) ─┘                ┘

LLM 优选：
  对量化候选做二次优选，量化分60% + LLM分40%

与现有策略关系：
  - 与 overnight_8step / funnel_strategy / llm_multisource 并列
  - 侧重"持续性+趋势"标的，非单日涨停回落
  - 结果写入 daily_candidates，source='main_uptrend'
"""
from .config import MainUptrendConfig, DEFAULT_CONFIG
from .engine import MainUptrendEngine

__version__ = "0.2.0"
