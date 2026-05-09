#!/usr/bin/env python3
"""
专业级优化方案3：L2订单簿模拟器与流动性过滤器

用户要求：在回测模块中增加"流动性过滤器"。参考专业机构做法：设定成交量占比限制
（例如单日成交量不得超过该股当日总成交的 5%-10%）。

核心功能：
1. L2订单簿模拟：模拟限价订单簿的深度和冲击成本
2. 流动性过滤器：基于成交量占比的交易限制
3. 冲击成本模型：基于订单簿深度的动态冲击成本
4. 成交概率估计：基于流动性的成交概率计算
5. 智能下单策略：根据流动性状况调整下单策略
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Optional, Union, Callable
from dataclasses import dataclass, field
from enum import Enum
import warnings
import logging
from scipy import interpolate

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LiquidityTier(Enum):
    """流动性层级"""
    EXTREME_HIGH = "extreme_high"    # 极端高流动性（大盘蓝筹）
    HIGH = "high"                    # 高流动性
    MEDIUM = "medium"                # 中等流动性
    LOW = "low"                      # 低流动性
    EXTREME_LOW = "extreme_low"      # 极端低流动性（小盘股）


class OrderType(Enum):
    """订单类型"""
    MARKET_ORDER = "market_order"        # 市价单
    LIMIT_ORDER = "limit_order"          # 限价单
    ICEBERG_ORDER = "iceberg_order"      # 冰山订单
    TWAP_ORDER = "twap_order"            # 时间加权平均价订单
    VWAP_ORDER = "vwap_order"            # 成交量加权平均价订单


class ExecutionResult(Enum):
    """执行结果"""
    FULLY_FILLED = "fully_filled"        # 完全成交
    PARTIALLY_FILLED = "partially_filled"  # 部分成交
    NOT_FILLED = "not_filled"            # 未成交
    CANCELLED = "cancelled"              # 已取消


@dataclass
class OrderBookLevel:
    """订单簿层级"""
    price: float                    # 价格
    volume: float                   # 成交量（股数）
    orders: int                     # 订单数量
    is_bid: bool                    # 是否为买盘


@dataclass
class OrderBookSnapshot:
    """订单簿快照"""
    timestamp: datetime
    symbol: str
    bid_levels: List[OrderBookLevel]   # 买盘层级（价格从高到低）
    ask_levels: List[OrderBookLevel]   # 卖盘层级（价格从低到高）
    mid_price: float                   # 中间价
    spread: float                      # 买卖价差
    total_bid_volume: float            # 总买盘量
    total_ask_volume: float            # 总卖盘量
    vwap: float                        # 成交量加权平均价


@dataclass
class LiquidityMetrics:
    """流动性指标"""
    daily_volume: float                 # 日成交量（股数）
    avg_daily_volume_20d: float         # 20日平均日成交量
    turnover_rate: float                # 换手率
    bid_ask_spread: float               # 买卖价差（百分比）
    market_depth_5levels: float         # 5档深度（金额）
    volume_imbalance: float             # 成交量不平衡度
    liquidity_tier: LiquidityTier       # 流动性层级
    adv_percentage_limit: float         # ADV百分比限制（如5%）


@dataclass
class TradeExecution:
    """交易执行结果"""
    order_id: str
    symbol: str
    order_type: OrderType
    side: str  # 'buy' or 'sell'
    quantity_requested: float           # 请求数量
    quantity_executed: float            # 执行数量
    avg_execution_price: float          # 平均执行价格
    benchmark_price: float              # 基准价格（如下单时的中间价）
    impact_cost: float                  # 冲击成本（百分比）
    execution_time: timedelta           # 执行时间
    execution_result: ExecutionResult
    liquidity_metrics: LiquidityMetrics
    order_book_impact: Dict[str, float]  # 对订单簿的影响


@dataclass
class LiquidityFilterResult:
    """流动性过滤器结果"""
    symbol: str
    trade_date: datetime
    requested_quantity: float
    max_allowed_quantity: float
    is_allowed: bool
    rejection_reason: Optional[str]
    suggested_quantity: float
    liquidity_metrics: LiquidityMetrics
    execution_estimate: Dict[str, float]


class L2OrderBookSimulator:
    """
    L2订单簿模拟器
    
    模拟限价订单簿的深度和结构，计算冲击成本和成交概率
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化L2订单簿模拟器
        
        Args:
            config: 配置参数
        """
        self.config = config or self._default_config()
        
        # 订单簿缓存
        self.order_book_cache: Dict[str, OrderBookSnapshot] = {}
        
        # 流动性数据缓存
        self.liquidity_cache: Dict[str, LiquidityMetrics] = {}
        
        logger.info("L2订单簿模拟器初始化完成")
    
    def _default_config(self) -> Dict[str, Any]:
        """默认配置"""
        return {
            # 订单簿模拟参数
            'order_book_levels': 10,                     # 订单簿层级数
            'default_spread_multiplier': 1.5,            # 默认价差乘数
            'volume_distribution_shape': 0.7,            # 成交量分布形状参数
            'min_order_size': 100,                       # 最小订单规模（股）
            
            # 冲击成本参数
            'impact_sensitivity': 0.0001,                # 冲击敏感度
            'depth_elasticity': 0.8,                     # 深度弹性
            'temporary_impact_factor': 0.6,              # 临时冲击因子
            'permanent_impact_factor': 0.4,              # 永久冲击因子
            
            # 流动性过滤器参数
            'adv_percentage_limit': 0.05,                # ADV百分比限制（5%）
            'max_daily_volume_percentage': 0.10,         # 最大日成交量百分比（10%）
            'min_liquidity_tier': LiquidityTier.LOW,     # 最低流动性层级要求
            'max_impact_cost': 0.02,                     # 最大允许冲击成本（2%）
            
            # 执行参数
            'market_order_fill_probability': 0.95,       # 市价单成交概率
            'limit_order_fill_probability_base': 0.7,    # 限价单基础成交概率
            'execution_time_base': timedelta(seconds=30),  # 基础执行时间
            'smart_order_routing': True,                 # 智能订单路由
        }
    
    def simulate_order_book(self,
                           symbol: str,
                           current_price: float,
                           daily_volume: float,
                           timestamp: datetime = None) -> OrderBookSnapshot:
        """
        模拟L2订单簿
        
        Args:
            symbol: 股票代码
            current_price: 当前价格
            daily_volume: 日成交量
            timestamp: 时间戳
            
        Returns:
            订单簿快照
        """
        
        if timestamp is None:
            timestamp = datetime.now()
        
        logger.debug(f"模拟订单簿: {symbol}, 价格={current_price}, 成交量={daily_volume}")
        
        # 计算买卖价差（基于流动性）
        spread_pct = self._calculate_spread(symbol, daily_volume)
        spread_amount = current_price * spread_pct
        
        # 生成买盘层级（价格从高到低）
        bid_levels = []
        bid_price = current_price - spread_amount / 2
        
        for i in range(self.config['order_book_levels']):
            # 价格递减
            level_price = bid_price * (1 - i * 0.001)  # 每档低0.1%
            
            # 成交量递减（越深的层级成交量越小）
            level_volume = self._calculate_level_volume(daily_volume, i, is_bid=True)
            
            # 订单数量估计
            level_orders = max(1, int(level_volume / 1000))  # 假设平均每单1000股
            
            bid_level = OrderBookLevel(
                price=level_price,
                volume=level_volume,
                orders=level_orders,
                is_bid=True
            )
            bid_levels.append(bid_level)
        
        # 生成卖盘层级（价格从低到高）
        ask_levels = []
        ask_price = current_price + spread_amount / 2
        
        for i in range(self.config['order_book_levels']):
            # 价格递增
            level_price = ask_price * (1 + i * 0.001)  # 每档高0.1%
            
            # 成交量递减
            level_volume = self._calculate_level_volume(daily_volume, i, is_bid=False)
            
            # 订单数量估计
            level_orders = max(1, int(level_volume / 1000))
            
            ask_level = OrderBookLevel(
                price=level_price,
                volume=level_volume,
                orders=level_orders,
                is_bid=False
            )
            ask_levels.append(ask_level)
        
        # 计算总量
        total_bid_volume = sum(level.volume for level in bid_levels)
        total_ask_volume = sum(level.volume for level in ask_levels)
        
        # 计算VWAP（成交量加权平均价）
        vwap = self._calculate_vwap(bid_levels, ask_levels)
        
        snapshot = OrderBookSnapshot(
            timestamp=timestamp,
            symbol=symbol,
            bid_levels=bid_levels,
            ask_levels=ask_levels,
            mid_price=current_price,
            spread=spread_pct,
            total_bid_volume=total_bid_volume,
            total_ask_volume=total_ask_volume,
            vwap=vwap
        )
        
        # 缓存订单簿快照
        self.order_book_cache[symbol] = snapshot
        
        return snapshot
    
    def _calculate_spread(self, symbol: str, daily_volume: float) -> float:
        """计算买卖价差（基于流动性）"""
        
        # 基础价差（bps）
        if daily_volume > 1e8:  # 日成交超过1亿
            base_spread = 0.0005  # 5bps
        elif daily_volume > 1e7:  # 日成交超过1千万
            base_spread = 0.0010  # 10bps
        elif daily_volume > 1e6:  # 日成交超过1百万
            base_spread = 0.0020  # 20bps
        elif daily_volume > 1e5:  # 日成交超过10万
            base_spread = 0.0050  # 50bps
        else:  # 日成交低于10万
            base_spread = 0.0100  # 100bps
        
        # 应用乘数
        spread = base_spread * self.config['default_spread_multiplier']
        
        return max(0.0002, min(0.05, spread))  # 限制在2bps到500bps之间
    
    def _calculate_level_volume(self, 
                               daily_volume: float, 
                               level: int, 
                               is_bid: bool) -> float:
        """计算订单簿层级成交量"""
        
        # 基础成交量（假设订单簿总深度约为日成交量的20%）
        base_depth = daily_volume * 0.2
        
        # 层级衰减因子（越深的层级成交量越小）
        decay_factor = np.exp(-self.config['volume_distribution_shape'] * level)
        
        # 层级成交量
        level_volume = base_depth * decay_factor / self.config['order_book_levels']
        
        # 添加随机性
        randomness = 0.8 + np.random.random() * 0.4  # 0.8到1.2之间的随机因子
        
        return max(self.config['min_order_size'], level_volume * randomness)
    
    def _calculate_vwap(self, 
                       bid_levels: List[OrderBookLevel],
                       ask_levels: List[OrderBookLevel]) -> float:
        """计算成交量加权平均价"""
        
        total_volume = 0
        total_value = 0
        
        # 买盘贡献
        for level in bid_levels:
            total_volume += level.volume
            total_value += level.volume * level.price
        
        # 卖盘贡献
        for level in ask_levels:
            total_volume += level.volume
            total_value += level.volume * level.price
        
        if total_volume > 0:
            return total_value / total_volume
        else:
            # 如果没有成交量，返回中间价
            if bid_levels and ask_levels:
                return (bid_levels[0].price + ask_levels[0].price) / 2
            else:
                return 0.0
    
    def calculate_impact_cost(self,
                             order_book: OrderBookSnapshot,
                             order_side: str,
                             order_quantity: float) -> Dict[str, float]:
        """
        计算冲击成本
        
        Args:
            order_book: 订单簿快照
            order_side: 订单方向 ('buy' or 'sell')
            order_quantity: 订单数量
            
        Returns:
            冲击成本分析
        """
        
        logger.debug(f"计算冲击成本: {order_book.symbol}, 方向={order_side}, 数量={order_quantity}")
        
        if order_side == 'buy':
            target_levels = order_book.ask_levels  # 买单消耗卖盘
            benchmark_price = order_book.bid_levels[0].price if order_book.bid_levels else order_book.mid_price
        else:  # 'sell'
            target_levels = order_book.bid_levels  # 卖单消耗买盘
            benchmark_price = order_book.ask_levels[0].price if order_book.ask_levels else order_book.mid_price
        
        # 模拟订单消耗订单簿
        remaining_quantity = order_quantity
        executed_volume = 0
        executed_value = 0
        levels_consumed = 0
        
        for level in target_levels:
            if remaining_quantity <= 0:
                break
            
            # 该层级可提供的成交量
            available_volume = level.volume
            
            # 实际成交量（取剩余需要量和可用量的最小值）
            trade_volume = min(remaining_quantity, available_volume)
            
            executed_volume += trade_volume
            executed_value += trade_volume * level.price
            remaining_quantity -= trade_volume
            levels_consumed += 1
        
        # 计算平均执行价格
        if executed_volume > 0:
            avg_execution_price = executed_value / executed_volume
        else:
            avg_execution_price = benchmark_price
        
        # 计算冲击成本
        if benchmark_price > 0:
            impact_cost_pct = (avg_execution_price - benchmark_price) / benchmark_price
            if order_side == 'sell':
                impact_cost_pct = -impact_cost_pct  # 卖单冲击成本为负
        else:
            impact_cost_pct = 0.0
        
        # 计算永久冲击和临时冲击
        permanent_impact = impact_cost_pct * self.config['permanent_impact_factor']
        temporary_impact = impact_cost_pct * self.config['temporary_impact_factor']
        
        # 计算成交比例
        fill_ratio = executed_volume / order_quantity if order_quantity > 0 else 0
        
        result = {
            'avg_execution_price': avg_execution_price,
            'benchmark_price': benchmark_price,
            'impact_cost_pct': impact_cost_pct,
            'permanent_impact_pct': permanent_impact,
            'temporary_impact_pct': temporary_impact,
            'total_impact_pct': permanent_impact + temporary_impact,
            'executed_volume': executed_volume,
            'remaining_quantity': remaining_quantity,
            'fill_ratio': fill_ratio,
            'levels_consumed': levels_consumed,
            'is_fully_filled': remaining_quantity <= 1e-6  # 考虑浮点误差
        }
        
        return result
    
    def estimate_execution_probability(self,
                                      order_book: OrderBookSnapshot,
                                      order_type: OrderType,
                                      order_side: str,
                                      order_quantity: float,
                                      limit_price: Optional[float] = None) -> float:
        """
        估计成交概率
        
        Args:
            order_book: 订单簿快照
            order_type: 订单类型
            order_side: 订单方向
            order_quantity: 订单数量
            limit_price: 限价单价格（仅限价单需要）
            
        Returns:
            成交概率（0-1）
        """
        
        # 基础成交概率
        if order_type == OrderType.MARKET_ORDER:
            base_probability = self.config['market_order_fill_probability']
        elif order_type == OrderType.LIMIT_ORDER:
            base_probability = self.config['limit_order_fill_probability_base']
        else:
            base_probability = 0.8  # 其他订单类型
        
        # 基于数量的调整
        if order_side == 'buy':
            available_volume = sum(level.volume for level in order_book.ask_levels)
        else:  # 'sell'
            available_volume = sum(level.volume for level in order_book.bid_levels)
        
        if available_volume > 0:
            quantity_ratio = order_quantity / available_volume
            quantity_adjustment = np.exp(-quantity_ratio * 2)  # 数量越大，成交概率越低
        else:
            quantity_adjustment = 0.0
        
        # 限价单价格调整
        price_adjustment = 1.0
        if order_type == OrderType.LIMIT_ORDER and limit_price is not None:
            if order_side == 'buy':
                # 买单：限价越高，成交概率越大
                best_ask = order_book.ask_levels[0].price if order_book.ask_levels else order_book.mid_price
                if limit_price >= best_ask:
                    price_adjustment = 1.0  # 限价优于或等于最优卖价
                else:
                    # 限价低于最优卖价，成交概率降低
                    price_distance = (best_ask - limit_price) / best_ask
                    price_adjustment = max(0.1, 1.0 - price_distance * 10)
            else:  # 'sell'
                # 卖单：限价越低，成交概率越大
                best_bid = order_book.bid_levels[0].price if order_book.bid_levels else order_book.mid_price
                if limit_price <= best_bid:
                    price_adjustment = 1.0  # 限价优于或等于最优买价
                else:
                    # 限价高于最优买价，成交概率降低
                    price_distance = (limit_price - best_bid) / best_bid
                    price_adjustment = max(0.1, 1.0 - price_distance * 10)
        
        # 计算最终成交概率
        final_probability = base_probability * quantity_adjustment * price_adjustment
        
        # 确保在合理范围内
        final_probability = max(0.0, min(1.0, final_probability))
        
        logger.debug(f"成交概率估计: 类型={order_type.value}, 方向={order_side}, "
                    f"数量={order_quantity}, 概率={final_probability:.2%}")
        
        return final_probability
    
    def simulate_trade_execution(self,
                                symbol: str,
                                order_type: OrderType,
                                side: str,
                                quantity: float,
                                current_price: float,
                                daily_volume: float,
                                limit_price: Optional[float] = None) -> TradeExecution:
        """
        模拟交易执行
        
        Args:
            symbol: 股票代码
            order_type: 订单类型
            side: 交易方向 ('buy' or 'sell')
            quantity: 数量
            current_price: 当前价格
            daily_volume: 日成交量
            limit_price: 限价单价格
            
        Returns:
            交易执行结果
        """
        
        # 生成订单簿
        order_book = self.simulate_order_book(symbol, current_price, daily_volume)
        
        # 获取流动性指标
        liquidity_metrics = self.get_liquidity_metrics(symbol, daily_volume)
        
        # 计算冲击成本
        impact_result = self.calculate_impact_cost(order_book, side, quantity)
        
        # 估计成交概率
        fill_probability = self.estimate_execution_probability(
            order_book, order_type, side, quantity, limit_price
        )
        
        # 模拟执行结果
        if fill_probability > np.random.random():
            # 成交
            executed_quantity = quantity * fill_probability  # 部分成交
            execution_result = ExecutionResult.PARTIALLY_FILLED
            
            if executed_quantity >= quantity * 0.99:  # 99%以上算完全成交
                executed_quantity = quantity
                execution_result = ExecutionResult.FULLY_FILLED
        else:
            # 未成交
            executed_quantity = 0
            execution_result = ExecutionResult.NOT_FILLED
        
        # 计算执行时间（基于订单大小和流动性）
        execution_time = self._calculate_execution_time(
            quantity, daily_volume, order_type, liquidity_metrics.liquidity_tier
        )
        
        # 生成交易执行结果
        execution = TradeExecution(
            order_id=f"{symbol}_{datetime.now().timestamp()}",
            symbol=symbol,
            order_type=order_type,
            side=side,
            quantity_requested=quantity,
            quantity_executed=executed_quantity,
            avg_execution_price=impact_result['avg_execution_price'],
            benchmark_price=impact_result['benchmark_price'],
            impact_cost=impact_result['total_impact_pct'],
            execution_time=execution