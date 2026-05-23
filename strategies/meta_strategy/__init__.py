"""
融合元策略模块
===============
六层漏斗编排，融合五大策略：
  - 八步隔夜法（核心决策）
  - 七步漏斗（基本面+流动性+大盘风控）
  - LLM多源策略（事件驱动加成）
  - 主升浪策略（启动信号+持续性）
  - 多因子扫描器（全市场广度扫描）

数据源：
  - PostgreSQL（生产环境）
  - Baostock（回测/无数据库环境）
"""

# 核心组件（始终可用）
from .position_manager import (
    PositionManager,
    PositionManagerConfig,
    DEFAULT_PM_CONFIG,
    Position,
    ExitSignal,
)

# Baostock 回测引擎（不依赖数据库）
from .baostock_backtester import (
    BaostockBacktester,
    MetaBacktestConfig,
    DEFAULT_BT_CONFIG as DEFAULT_BT_CONFIG_BS,
    run_backtest,
)

# PostgreSQL 元引擎（需要数据库）
try:
    from .meta_engine import (
        MetaStrategyEngine,
        MetaStrategyConfig,
        DEFAULT_META_CONFIG,
        check_market_risk,
        run_multi_factor_scan,
        run_fundamental_liquidity_filter,
        detect_launch_signals,
        get_llm_boost,
        compute_overnight_score,
        normalize_scores,
    )
    from .meta_backtester import (
        MetaBacktester,
        BacktestConfig,
        DEFAULT_BT_CONFIG,
    )
except Exception:
    pass

__all__ = [
    'PositionManager', 'PositionManagerConfig', 'DEFAULT_PM_CONFIG',
    'BaostockBacktester', 'MetaBacktestConfig', 'run_backtest',
]
