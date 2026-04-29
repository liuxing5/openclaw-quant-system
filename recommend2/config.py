"""
全局配置 — 主升前夜策略
=========================
符合 openclaw-quant-system 规范:
  - 数据源: AKShare (Tushare 禁用)
  - MACD 8/17/9 短线参数
  - 时区: Asia/Shanghai
  - 风控: 2% 单笔止损, 30% 现金, 自动熔断
"""
from __future__ import annotations
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


# ============================================================
# 筛选器配置
# ============================================================
@dataclass
class ScreenerConfig:
    # L1 底部定义
    # 阈值说明:
    #   bottom_drawdown_min=0.25 适合震荡偏高位市场(2026 当前)
    #   熊市末期可调到 0.35-0.40 收紧;牛市初期可调到 0.20 放松
    # 高点窗口说明:
    #   high_lookback_days=500 (~2 年)能识别真正的"深度回撤大底"
    #   250 日窗口只能看到近 1 年高点,会漏掉真正经历主跌段的票
    #   (例如药明康德从 2021 年 178 元跌到 2024 年 36 元,250 日窗口看不到)
    bottom_drawdown_min: float = 0.25
    bottom_rebound_max: float = 0.20
    high_lookback_days: int = 500
    low_lookback_days: int = 60

    # L2 低位涨停
    limit_up_lookback: int = 60
    limit_up_low_zone: float = 0.15

    # L3 跳空缺口
    gap_min_pct: float = 0.015
    gap_no_fill_days: int = 5
    gap_volume_ratio: float = 1.5
    gap_search_window: int = 20

    # L4 连阳
    consecutive_yang_min: int = 4
    yang_no_break_ma5: bool = True
    yang_search_window: int = 10

    # L5/L6 量能
    breakout_volume_mult: float = 2.0
    sustain_volume_mult: float = 1.5
    sustain_days_required: int = 2
    sustain_window: int = 3

    # L7 MACD (项目标准短线参数)
    macd_fast: int = 8
    macd_slow: int = 17
    macd_signal: int = 9

    # L8.5 龙虎榜机构席位 (新增第 12 层)
    lhb_lookback_days: int = 30          # 近 30 日龙虎榜
    lhb_inst_net_min: float = 0          # 机构席位累计净买 > 0
    lhb_required: bool = False           # 默认不强制(避免数据缺失误杀)

    # L9 高位剔除
    above_ma60_max: float = 0.25

    # L10 风控
    listing_days_min: int = 250
    market_cap_min: float = 20e8
    market_cap_max: float = 500e8

    # L11 大盘
    index_symbol: str = "sh.000300"   # baostock 格式
    index_ma: int = 20

    # 综合
    # baostock 数据源: L8/L8.5 默认跳过(返回 None),实际可计分层为 10 层(L1-L7, L9-L11)
    # 触发阈值 8/10 = 80% 通过率,与 akshare 时的 10/12 等价
    min_layers_to_trigger: int = 8
    allow_l8_missing: bool = True     # baostock 无主力资金流接口,必须允许
    allow_lhb_missing: bool = True    # baostock 无龙虎榜接口,必须允许


# ============================================================
# 退出器配置(对称的出场逻辑)
# ============================================================
@dataclass
class ExitorConfig:
    # 硬性止损 (项目规范: 2%)
    hard_stop_pct: float = 0.02

    # 移动止盈
    trailing_activate_pct: float = 0.08   # 浮盈 8% 后激活
    trailing_giveback_pct: float = 0.05   # 从最高点回撤 5% 离场

    # 时间止损
    max_holding_days: int = 15

    # 量价信号
    macd_dead_cross_exit: bool = True
    break_ma8_with_volume: bool = True    # 跌破 8 日均线且放量
    volume_climax_exit: bool = True       # 量比 ≥3 且收阴(出货嫌疑)


# ============================================================
# 回测/组合配置
# ============================================================
@dataclass
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    cash_reserve_ratio: float = 0.30      # 项目规范: 30% 现金
    max_concurrent_positions: int = 5
    risk_per_trade: float = 0.02          # 单笔最大风险 2%

    # 交易成本
    commission_rate: float = 0.00025      # 万 2.5
    stamp_tax_rate: float = 0.0005        # 千 0.5(卖出)
    slippage_bps: float = 5               # 5 个基点

    # T+1
    enforce_t_plus_1: bool = True

    # 熔断
    daily_loss_circuit_breaker: float = 0.05    # 单日亏损 5% 暂停
    drawdown_circuit_breaker: float = 0.15      # 总回撤 15% 暂停

    # 一字板剔除
    skip_one_word_limit: bool = True       # 跳过次日开盘即涨停的标的(买不到)
    one_word_open_pct: float = 0.095       # 次日开盘涨幅 ≥ 9.5% 视为一字板风险

    # Walk-forward
    train_months: int = 12
    test_months: int = 3
    rebalance_freq: str = "W"             # 周度调仓


# ============================================================
# 数据缓存配置
# ============================================================
@dataclass
class DataConfig:
    cache_dir: str = "./data"
    cache_ttl_hours: int = 6              # 盘中数据 6 小时刷新
    request_timeout: int = 30
    max_retries: int = 3
    retry_backoff: float = 1.5


# ============================================================
# 龙头断板策略配置(方案 C 独立策略 2)
# ============================================================
@dataclass
class DragonConfig:
    """
    龙头断板捕捉策略
    核心哲学: 板块热度 + 龙头属性 + 有效断板 → 中线启动信号

    与主升前夜的区别:
      - 主升前夜看"周月级底部",数据窗口 500 日
      - 龙头断板看"板块短期节奏",数据窗口 60 日
      - 主升前夜 12 层 + 85% 通过率门槛
      - 龙头断板 9 层 + 7/9 门槛(更多信号)

    L1  涨停过滤         — 近 20 日出现过涨停(否则算不上连板题材)
    L2  连板高度         — 近 10 日内连板数 ≥ 2(不要 1 日游)
    L3  连板时近期强势   — 连板期间累计涨幅 ≥ 15%
    L4  板块同步度       — 近 5 日同步涨停数 ≥ 3(同涨停日有 ≥3 只票涨停)
    L5  有效断板         — 最近 1-3 日"断板日":未涨停但收阳 或 小幅下跌(-3% ~ +9%)
    L6  断板日放量       — 断板日成交额 ≥ 前一日 1.5 倍
    L7  MACD 动能未死    — MACD(8/17/9) DIF > DEA
    L8  非一字跌停后     — 近 3 日没出现一字跌停(否则是炸板)
    L9  风控过滤         — 非 ST/退市, 上市 ≥60 日, 大盘健康
    """
    # L1 涨停过滤
    limit_up_lookback: int = 20
    limit_up_pct_main: float = 0.097     # 主板涨停阈值
    limit_up_pct_chinext: float = 0.197  # 创业板/科创板

    # L2 连板高度
    consecutive_limit_up_min: int = 2
    consecutive_lookback: int = 10

    # L3 连板期间累计涨幅
    cumulative_gain_min: float = 0.15

    # L4 板块同步度(隐式聚类: 看"同日涨停数")
    sector_sync_lookback: int = 5
    sector_sync_min_count: int = 3       # 至少有 3 只股票在同一天涨停过
    sector_sync_sample_size: int = 500   # 随机抽样标的用于同步度判断

    # L5 有效断板
    break_board_lookback: int = 3        # 最近 3 日内有效断板即可
    break_board_max_pct: float = 0.09    # 断板日涨幅 ≤ 9%(未涨停)
    break_board_min_pct: float = -0.03   # 断板日跌幅 ≥ -3%(未大跌)

    # L6 断板日放量
    break_volume_ratio: float = 1.5

    # L7 MACD
    macd_fast: int = 8
    macd_slow: int = 17
    macd_signal: int = 9

    # L8 炸板保护
    crash_lookback: int = 3
    one_word_down_threshold: float = -0.095

    # L9 风控
    listing_days_min: int = 60          # 龙头断板可以抓次新股
    index_symbol: str = "sh.000300"
    index_ma: int = 20

    # 综合判定
    min_layers_to_trigger: int = 7       # 9 层中 7 层通过

    # 仓位/止损(比主升前夜更紧,因为波动更大)
    stop_loss_pct: float = 0.05          # 5% 硬止损
    position_size_pct: float = 0.15      # 单仓 15% 上限(vs 主升前夜的 25%)
    max_holding_days: int = 5            # 龙头断板是短线,最多持 5 日
