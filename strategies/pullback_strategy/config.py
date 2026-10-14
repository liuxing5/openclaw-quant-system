"""回调买入法 — 参数配置"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PullbackConfig:
    # ---- 步骤1: 强势股筛选 ----
    # 2026-10-13 参数审查修复:
    #   - 原 20 日动能 ≥20% 落在 A 股"短期反转"区间（动量证据: <1周有效、
    #     更长反转；中期 3~6 个月形成期才有动量证据）→ 改为 120 日形成期
    #   - 原绝对阈值 20% 随 regime 漂移 → 改为当日截面百分位
    momentum_lookback: int = 120      # 动能回看窗口(交易日, 中期动量形成期)
    min_momentum_pctile: float = 70.0 # 当日截面动能百分位 ≥ 70（前30%）
    min_momentum_pct: float = 0.0     # 绝对下限: 累计涨幅 > 0（只作正动量兜底）
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

    # ---- 步骤4: 波动率止损入场 ----
    stop_lookback: int = 5            # 近5日低点
    stop_below_support_pct: float = 1.0  # 止损设在支撑下方 1%
    atr_stop_mult: float = 1.5        # 止损至少 1.5×ATR14 宽（2026-10-13: 原3%比日噪声窄，67%被噪声止损）
    max_risk_pct: float = 6.0         # 风险上限（原3%→6%，与1.5ATR下限配套）
    # 2026-10-14 收益改造: RR 2→3（离线出场网格: 全样本聚类 t 1.46→1.85/1.70，
    # 叠加宽度门控后 t=2.86*，验证窗 +0.27%；固定 2R 小目标是公开策略公认的
    # "截断利润"缺陷——O'Neil/Turtle 均主张让赢家跑）
    reward_risk: float = 3.0          # 目标 = 入场 + 3×风险

    # ---- 市场宽度门控 (2026-10-14 收益改造) ----
    # Faber/O'Neil 市场内在环境过滤: 信号日全市场 %站上MA20 低于阈值时不开新仓。
    # 证据(12817 笔, 训练<2026-04 / 验证>=2026-04): b20>=0.4 使全样本聚类 t
    # 1.04→2.07*, 验证窗 -1.12→+0.27；低宽度日止损率显著更高(2026-06/07 宽度
    # 崩塌月均值 -2.65%~-0.46%)。0=关闭门控。
    min_market_breadth: float = 0.4

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
