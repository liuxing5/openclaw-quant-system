#!/usr/bin/env python3
"""
冲击成本滑点模型 - 动态计算交易成本
A股中小盘股在快速拉升时，买入动作本身会推高股价
滑点 = (1/2) * 买卖价差 或 根据成交量占比动态计算
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')
from scipy import stats

class MarketRegime(Enum):
    """市场状态"""
    NORMAL = "正常"          # 正常市场
    VOLATILE = "高波动"      # 高波动市场
    RAPID_RISING = "快速拉升"  # 快速拉升（中小盘股常见）
    RAPID_FALLING = "快速下跌" # 快速下跌
    LOW_LIQUIDITY = "低流动性" # 低流动性（如尾盘）

class StockLiquidity(Enum):
    """股票流动性分类"""
    HIGH = "高流动性"      # 大盘股，日成交额>10亿
    MEDIUM = "中等流动性"  # 中盘股，日成交额1-10亿
    LOW = "低流动性"       # 小盘股，日成交额<1亿
    ILLIQUID = "极低流动性" # 僵尸股，日成交额<1000万

@dataclass
class MarketSnapshot:
    """市场快照"""
    timestamp: pd.Timestamp
    symbol: str
    # 价格信息
    bid_price: float          # 买一价
    ask_price: float          # 卖一价
    last_price: float         # 最新价
    # 挂单信息
    bid_volume: float         # 买一量
    ask_volume: float         # 卖一量
    # 成交信息
    volume: float             # 成交量
    amount: float             # 成交额
    # 市场数据
    market_volume: float      # 市场总成交量
    market_amount: float      # 市场总成交额
    # 衍生指标
    bid_ask_spread: float     # 买卖价差
    mid_price: float          # 中间价
    volume_ratio: float       # 成交量占比（个股/市场）
    liquidity_score: float    # 流动性评分 (0-1)

@dataclass
class SlippageParameters:
    """滑点参数"""
    # 基础参数
    base_slippage: float = 0.002  # 基础滑点 0.2%
    spread_multiplier: float = 0.5  # 买卖价差乘数 (1/2)
    volume_sensitivity: float = 0.01  # 成交量敏感度
    
    # 市场状态调整
    regime_multipliers: Dict[MarketRegime, float] = None
    
    # 流动性调整
    liquidity_multipliers: Dict[StockLiquidity, float] = None
    
    # 时间调整（尾盘效应）
    time_of_day_multipliers: Dict[str, float] = None
    
    def __post_init__(self):
        if self.regime_multipliers is None:
            self.regime_multipliers = {
                MarketRegime.NORMAL: 1.0,
                MarketRegime.VOLATILE: 1.5,
                MarketRegime.RAPID_RISING: 2.0,
                MarketRegime.RAPID_FALLING: 1.8,
                MarketRegime.LOW_LIQUIDITY: 2.5
            }
        
        if self.liquidity_multipliers is None:
            self.liquidity_multipliers = {
                StockLiquidity.HIGH: 0.5,
                StockLiquidity.MEDIUM: 1.0,
                StockLiquidity.LOW: 2.0,
                StockLiquidity.ILLIQUID: 5.0
            }
        
        if self.time_of_day_multipliers is None:
            self.time_of_day_multipliers = {
                'open_30min': 1.2,      # 开盘30分钟
                'midday': 1.0,          # 盘中
                'close_30min': 1.5,     # 收盘30分钟
                'other': 1.0
            }

@dataclass
class SlippageEstimate:
    """滑点估计"""
    timestamp: pd.Timestamp
    symbol: str
    action: str  # 'buy' or 'sell'
    order_size: float  # 订单金额
    order_volume: float  # 订单股数
    
    # 输入参数
    market_snapshot: MarketSnapshot
    liquidity_class: StockLiquidity
    market_regime: MarketRegime
    time_of_day: str
    
    # 计算结果
    bid_ask_spread_bps: float  # 买卖价差（基点）
    volume_impact_bps: float   # 成交量冲击（基点）
    regime_impact_bps: float   # 市场状态影响（基点）
    liquidity_impact_bps: float # 流动性影响（基点）
    time_impact_bps: float     # 时间影响（基点）
    
    total_slippage_bps: float  # 总滑点（基点）
    total_slippage_pct: float  # 总滑点百分比
    
    # 执行价格
    estimated_price: float     # 估计执行价格
    price_impact_pct: float    # 价格影响百分比
    
    # 置信度
    confidence_score: float    # 置信度评分 (0-1)

class ImpactCostSlippageModel:
    """冲击成本滑点模型"""
    
    def __init__(self, parameters: Optional[SlippageParameters] = None):
        """
        初始化滑点模型
        
        Args:
            parameters: 滑点参数
        """
        self.parameters = parameters or SlippageParameters()
        
        # 历史数据缓存
        self.history_cache: Dict[str, List[MarketSnapshot]] = {}
        
        # 模型训练数据
        self.training_data = []
        
        # 性能统计
        self.performance_stats = {
            'estimates_made': 0,
            'avg_slippage_bps': 0.0,
            'max_slippage_bps': 0.0,
            'min_slippage_bps': float('inf')
        }
    
    def classify_liquidity(self, market_snapshot: MarketSnapshot) -> StockLiquidity:
        """
        分类股票流动性
        
        Args:
            market_snapshot: 市场快照
            
        Returns:
            流动性分类
        """
        daily_amount = market_snapshot.amount
        
        if daily_amount > 1e9:  # > 10亿
            return StockLiquidity.HIGH
        elif daily_amount > 1e8:  # > 1亿
            return StockLiquidity.MEDIUM
        elif daily_amount > 1e7:  # > 1000万
            return StockLiquidity.LOW
        else:
            return StockLiquidity.ILLIQUID
    
    def detect_market_regime(self, symbol: str, timestamp: pd.Timestamp) -> MarketRegime:
        """
        检测市场状态
        
        Args:
            symbol: 股票代码
            timestamp: 时间戳
            
        Returns:
            市场状态
        """
        # 获取历史数据
        history = self.history_cache.get(symbol, [])
        if len(history) < 10:
            return MarketRegime.NORMAL
        
        # 提取最近数据
        recent_snapshots = history[-10:]
        
        # 计算价格变化率
        prices = [s.last_price for s in recent_snapshots]
        price_changes = np.diff(prices) / prices[:-1]
        
        # 计算波动率
        volatility = np.std(price_changes) * np.sqrt(252) if len(price_changes) > 1 else 0.0
        
        # 计算趋势
        if len(prices) >= 3:
            # 最近3个点的斜率
            x = np.arange(3)
            y = prices[-3:]
            slope, _ = np.polyfit(x, y, 1)
            trend_strength = slope / prices[-1]
        else:
            trend_strength = 0.0
        
        # 计算成交量变化
        volumes = [s.volume for s in recent_snapshots]
        volume_changes = np.diff(volumes) / volumes[:-1] if len(volumes) > 1 else [0.0]
        avg_volume_change = np.mean(volume_changes) if len(volume_changes) > 0 else 0.0
        
        # 判断市场状态
        if volatility > 0.4:  # 年化波动率>40%
            if trend_strength > 0.02:  # 上涨趋势强
                return MarketRegime.RAPID_RISING
            elif trend_strength < -0.02:  # 下跌趋势强
                return MarketRegime.RAPID_FALLING
            else:
                return MarketRegime.VOLATILE
        elif avg_volume_change < -0.5:  # 成交量大幅萎缩
            return MarketRegime.LOW_LIQUIDITY
        else:
            return MarketRegime.NORMAL
    
    def get_time_of_day(self, timestamp: pd.Timestamp) -> str:
        """
        获取交易时段
        
        Args:
            timestamp: 时间戳
            
        Returns:
            交易时段分类
        """
        hour = timestamp.hour
        minute = timestamp.minute
        
        # A股交易时间: 9:30-11:30, 13:00-15:00
        if hour == 9 and minute >= 30:
            return 'open_30min'
        elif hour == 11 and minute <= 30:
            return 'midday'
        elif hour == 14 and minute >= 30:
            return 'close_30min'
        else:
            return 'other'
    
    def calculate_bid_ask_spread_impact(self, market_snapshot: MarketSnapshot) -> float:
        """
        计算买卖价差影响
        
        Args:
            market_snapshot: 市场快照
            
        Returns:
            价差影响（基点）
        """
        if market_snapshot.bid_price > 0 and market_snapshot.ask_price > 0:
            spread_pct = (market_snapshot.ask_price - market_snapshot.bid_price) / market_snapshot.mid_price
            # 公式: (1/2) * 买卖价差
            impact = self.parameters.spread_multiplier * spread_pct * 10000  # 转为基点
        else:
            # 默认价差: 0.1%
            impact = 0.001 * self.parameters.spread_multiplier * 10000
        
        return impact
    
    def calculate_volume_impact(self, market_snapshot: MarketSnapshot, 
                               order_volume: float) -> float:
        """
        计算成交量冲击
        
        Args:
            market_snapshot: 市场快照
            order_volume: 订单股数
            
        Returns:
            成交量冲击（基点）
        """
        if market_snapshot.volume <= 0:
            return 0.0
        
        # 计算成交量占比
        volume_ratio = order_volume / market_snapshot.volume
        
        # 简化模型: 冲击 = 敏感度 * 成交量占比^2
        # 实际可以使用更复杂的模型，如平方根法则
        impact = self.parameters.volume_sensitivity * (volume_ratio ** 2) * 10000
        
        # 限制最大冲击
        max_impact = 5.0 * 100  # 最大500基点
        return min(impact, max_impact)
    
    def calculate_regime_impact(self, market_regime: MarketRegime) -> float:
        """
        计算市场状态影响
        
        Args:
            market_regime: 市场状态
            
        Returns:
            状态影响（基点）
        """
        multiplier = self.parameters.regime_multipliers.get(market_regime, 1.0)
        
        # 基础影响: 正常市场10基点
        base_impact = 10.0
        impact = base_impact * (multiplier - 1.0)
        
        return impact
    
    def calculate_liquidity_impact(self, liquidity_class: StockLiquidity) -> float:
        """
        计算流动性影响
        
        Args:
            liquidity_class: 流动性分类
            
        Returns:
            流动性影响（基点）
        """
        multiplier = self.parameters.liquidity_multipliers.get(liquidity_class, 1.0)
        
        # 基础影响: 中等流动性20基点
        base_impact = 20.0
        impact = base_impact * (multiplier - 1.0)
        
        return impact
    
    def calculate_time_impact(self, time_of_day: str) -> float:
        """
        计算时间影响
        
        Args:
            time_of_day: 交易时段
            
        Returns:
            时间影响（基点）
        """
        multiplier = self.parameters.time_of_day_multipliers.get(time_of_day, 1.0)
        
        # 基础影响: 正常时段5基点
        base_impact = 5.0
        impact = base_impact * (multiplier - 1.0)
        
        return impact
    
    def estimate_slippage(self, 
                         symbol: str,
                         timestamp: pd.Timestamp,
                         action: str,
                         order_size: float,
                         order_volume: float,
                         market_snapshot: MarketSnapshot) -> SlippageEstimate:
        """
        估计滑点
        
        Args:
            symbol: 股票代码
            timestamp: 时间戳
            action: 交易方向 'buy' or 'sell'
            order_size: 订单金额
            order_volume: 订单股数
            market_snapshot: 市场快照
            
        Returns:
            滑点估计
        """
        # 分类和检测
        liquidity_class = self.classify_liquidity(market_snapshot)
        market_regime = self.detect_market_regime(symbol, timestamp)
        time_of_day = self.get_time_of_day(timestamp)
        
        # 计算各项影响
        bid_ask_impact = self.calculate_bid_ask_spread_impact(market_snapshot)
        volume_impact = self.calculate_volume_impact(market_snapshot, order_volume)
        regime_impact = self.calculate_regime_impact(market_regime)
        liquidity_impact = self.calculate_liquidity_impact(liquidity_class)
        time_impact = self.calculate_time_impact(time_of_day)
        
        # 计算总滑点（基点）
        total_slippage_bps = (
            bid_ask_impact + 
            volume_impact + 
            regime_impact + 
            liquidity_impact + 
            time_impact
        )
        
        # 转为百分比
        total_slippage_pct = total_slippage_bps / 10000
        
        # 计算执行价格
        if action == 'buy':
            estimated_price = market_snapshot.ask_price * (1 + total_slippage_pct)
        else:  # sell
            estimated_price = market_snapshot.bid_price * (1 - total_slippage_pct)
        
        # 计算价格影响
        price_impact_pct = abs(estimated_price - market_snapshot.last_price) / market_snapshot.last_price
        
        # 计算置信度
        confidence_score = self._calculate_confidence(
            market_snapshot, liquidity_class, market_regime
        )
        
        # 更新性能统计
        self._update_performance_stats(total_slippage_bps)
        
        # 创建滑点估计
        estimate = SlippageEstimate(
            timestamp=timestamp,
            symbol=symbol,
            action=action,
            order_size=order_size,
            order_volume=order_volume,
            
            market_snapshot=market_snapshot,
            liquidity_class=liquidity_class,
            market_regime=market_regime,
            time_of_day=time_of_day,
            
            bid_ask_spread_bps=bid_ask_impact,
            volume_impact_bps=volume_impact,
            regime_impact_bps=regime_impact,
            liquidity_impact_bps=liquidity_impact,
            time_impact_bps=time_impact,
            
            total_slippage_bps=total_slippage_bps,
            total_slippage_pct=total_slippage_pct,
            
            estimated_price=estimated_price,
            price_impact_pct=price_impact_pct,
            
            confidence_score=confidence_score
        )
        
        # 缓存历史数据
        if symbol not in self.history_cache:
            self.history_cache[symbol] = []
        self.history_cache[symbol].append(market_snapshot)
        
        # 保持历史长度
        if len(self.history_cache[symbol]) > 1000:
            self.history_cache[symbol] = self.history_cache[symbol][-1000:]
        
        return estimate
    
    def _calculate_confidence(self, 
                            market_snapshot: MarketSnapshot,
                            liquidity_class: StockLiquidity,
                            market_regime: MarketRegime) -> float:
        """计算置信度"""
        confidence = 1.0
        
        # 数据完整性
        if market_snapshot.bid_price <= 0 or market_snapshot.ask_price <= 0:
            confidence *= 0.5
        
        if market_snapshot.volume <= 0:
            confidence *= 0.7
        
        # 流动性影响
        if liquidity_class in [StockLiquidity.LOW, StockLiquidity.ILLIQUID]:
            confidence *= 0.8
        
        # 市场状态影响
        if market_regime in [MarketRegime.RAPID_RISING, MarketRegime.RAPID_FALLING]:
            confidence *= 0.7
        
        return confidence
    
    def _update_performance_stats(self, slippage_bps: float):
        """更新性能统计"""
        self.performance_stats['estimates_made'] += 1
        self.performance_stats['avg_slippage_bps'] = (
            (self.performance_stats['avg_slippage_bps'] * (self.performance_stats['estimates_made'] - 1) + slippage_bps) /
            self.performance_stats['estimates_made']
        )
        self.performance_stats['max_slippage_bps'] = max(
            self.performance_stats['max_slippage_bps'], slippage_bps
        )
        self.performance_stats['min_slippage_bps'] = min(
            self.performance_stats['min_slippage_bps'], slippage_bps
        )
    
    def generate_slippage_report(self) -> Dict[str, Any]:
        """生成滑点报告"""
        report = {
            'performance': self.performance_stats,
            'parameter_summary': {
                'base_slippage': self.parameters.base_slippage,
                'spread_multiplier': self.parameters.spread_multiplier,
                'volume_sensitivity': self.parameters.volume_sensitivity
            },
            'regime_multipliers': {
                regime.value: multiplier 
                for regime, multiplier in self.parameters.regime_multipliers.items()
            },
            'liquidity_multipliers': {
                liquidity.value: multiplier
                for liquidity, multiplier in self.parameters.liquidity_multipliers.items()
            },
            'time_multipliers': self.parameters.time_of_day_multipliers,
            'recent_estimates': self._get_recent_estimates(10)
        }
        
        return report
    
    def _get_recent_estimates(self, n: int) -> List[Dict[str, Any]]:
        """获取最近估计"""
        # 这里应该从实际估计记录中获取
        # 简化处理：返回空列表
        return []

# ========== 简化版滑点模型（用于快速集成） ==========

class SimpleImpactSlippageModel:
    """简化版冲击成本滑点模型"""
    
    @staticmethod
    def calculate_slippage(symbol: str, price: float, volume: float, 
                          action: str = 'buy', market_volume: float = 1e8,
                          bid_ask_spread: float = 0.001) -> float:
        """
        计算简化滑点
        
        Args:
            symbol: 股票代码
            price: 当前价格
            volume: 交易股数
            action: 交易方向
            market_volume: 市场成交量
            bid_ask_spread: 买卖价差比例
            
        Returns:
            滑点金额
        """
        # 基础滑点: 1/2 * 买卖价差
        spread_slippage = price * bid_ask_spread * 0.5
        
        # 成交量冲击滑点
        if market_volume > 0:
            volume_ratio = volume / market_volume
            volume_slippage = price * 0.01 * (volume_ratio ** 0.5)  # 平方根法则
        else:
            volume_slippage = price * 0.005  # 默认0.5%
        
        # 流动性调整（根据股票代码判断）
        liquidity_multiplier = SimpleImpactSlippageModel._get_liquidity_multiplier(symbol)
        
        # 总滑点
        total_slippage = (spread_slippage + volume_slippage) * liquidity_multiplier
        
        # 方向调整（买入通常滑点更大）
        if action == 'buy':
            total_slippage *= 1.2
        
        return total_slippage
    
    @staticmethod
    def _get_liquidity_multiplier(symbol: str) -> float:
        """获取流动性乘数"""
        # 根据股票代码前缀判断
        if symbol.startswith('60'):  # 上证主板
            return 0.8
        elif symbol.startswith('00'):  # 深证主板
            return 0.9
        elif symbol.startswith('30'):  # 创业板
            return 1.2
        elif symbol.startswith('68'):  # 科创板
            return 1.5
        else:
            return 1.0

# ========== 示例使用 ==========

def example_usage():
    """示例使用方法"""
    print("冲击成本滑点模型示例")
    print("=" * 60)
    
    # 创建市场快照
    snapshot = MarketSnapshot(
        timestamp=pd.Timestamp('2024-01-15 10:30:00'),
        symbol='300750',
        bid_price=180.5,
        ask_price=180.8,
        last_price=180.6,
        bid_volume=50000,
        ask_volume=30000,
        volume=1500000,
        amount=271e6,  # 2.71亿
        market_volume=5e9,  # 市场总成交量50亿股
        market_amount=500e9,  # 市场总成交额5000亿
        bid_ask_spread=0.3,  # 0.3元
        mid_price=180.65,
        volume_ratio=1500000 / 5e9,
        liquidity_score=0.8
    )
    
    # 创建滑点模型
    model = ImpactCostSlippageModel()
    
    # 估计滑点
    print("估计滑点示例:")
    print(f"股票: {snapshot.symbol}")
    print(f"时间: {snapshot.timestamp}")
    print(f"最新价: {snapshot.last_price:.2f}")
    print(f"买卖价差: {snapshot.bid_ask_spread:.2f} ({snapshot.bid_ask_spread/snapshot.mid_price*10000:.0f}基点)")
    print()
    
    # 不同订单规模的滑点估计
    order_sizes = [100000, 500000, 2000000]  # 10万, 50万, 200万
    
    for order_size in order_sizes:
        order_volume = order_size / snapshot.last_price
        
        estimate = model.estimate_slippage(
            symbol=snapshot.symbol,
            timestamp=snapshot.timestamp,
            action='buy',
            order_size=order_size,
            order_volume=order_volume,
            market_snapshot=snapshot
        )
        
        print(f"订单金额: {order_size:,.0f}元")
        print(f"  总滑点: {estimate.total_slippage_bps:.0f}基点 ({estimate.total_slippage_pct*100:.3f}%)")
        print(f"  估计执行价: {estimate.estimated_price:.2f}")
        print(f"  价格影响: {estimate.price_impact_pct*100:.3f}%")
        print(f"  置信度: {estimate.confidence_score:.2f}")
        
        # 详细分解
        print(f"  滑点分解:")
        print(f"    买卖价差: {estimate.bid_ask_spread_bps:.0f}基点")
        print(f"    成交量冲击: {estimate.volume_impact_bps:.0f}基点")
        print(f"    市场状态: {estimate.regime_impact_bps:.0f}基点 ({estimate.market_regime.value})")
        print(f"    流动性: {estimate.liquidity_impact_bps:.0f}基点 ({estimate.liquidity_class.value})")
        print(f"    交易时段: {estimate.time_impact_bps:.0f}基点 ({estimate.time_of_day})")
        print()
    
    # 简化版模型示例
    print("简化版模型示例:")
    simple_slippage = SimpleImpactSlippageModel.calculate_slippage(
        symbol='300750',
        price=180.6,
        volume=10000,
        action='buy',
        market_volume=5e9,
        bid_ask_spread=0.001
    )
    
    print(f"股票: 300750")
    print(f"价格: 180.6")
    print(f"交易: 10000股 (买入)")
    print(f"滑点金额: {simple_slippage:.4f}元/股")
    print(f"滑点比例: {simple_slippage/180.6*100:.3f}%")
    
    # 生成报告
    report = model.generate_slippage_report()
    print(f"\n模型统计:")
    print(f"  估计次数: {report['performance']['estimates_made']}")
    print(f"  平均滑点: {report['performance']['avg_slippage_bps']:.0f}基点")
    print(f"  最大滑点: {report['performance']['max_slippage_bps']:.0f}基点")
    print(f"  最小滑点: {report['performance']['min_slippage_bps']:.0f}基点")

if __name__ == "__main__":
    example_usage()