"""
漏斗选股策略 v1.0 — 七步闭环框架
====================================
七层漏斗：
  Layer 0: 大盘风控（盘前）—— 上涨家数 + 全A指数>20EMA
  Layer 1: 硬性防雷 —— ST/财务质量/营收
  Layer 2: 流动性筛选 —— 成交额/市值/换手
  Layer 3: 趋势结构过滤 —— 周线/EMA排列/年线
  Layer 4: 动能与买入信号 —— K线形态/VWAP/量比/乖离
  Layer 5: 人气精选 —— 综合评分+人气榜
  Layer 6: 刚性风控 —— ATR止损/时段/盈亏比

核心纪律：每晚复盘回测，任一步不满足即推倒重来，
连续3次止损失败暂停交易一天。

吸收策略来源：
  ① 巴菲特准则/基本面 ② 20周保命法/均线多头/年线 ③ 隔夜八步法
  ④ 严格执行纪律/复盘强化 ⑤ 右侧交易/VWAP/价格行为
  ⑥ 人气榜/AI共识 ⑦ 海龟风控/ATR
"""
from .funnel_config import FunnelConfig, DEFAULT_FUNNEL_CONFIG
from .funnel_engine import FunnelEngine, run_funnel_strategy

__all__ = [
    'FunnelConfig',
    'DEFAULT_FUNNEL_CONFIG',
    'FunnelEngine',
    'run_funnel_strategy',
]
