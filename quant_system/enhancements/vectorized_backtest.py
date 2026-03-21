#!/usr/bin/env python3
"""
向量化回测加速引擎 - 避免Python循环，使用Pandas/Numpy向量化操作
目标：5分钟内完成10支股票3年回测
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Any, Optional, Callable
import warnings
warnings.filterwarnings('ignore')
from dataclasses import dataclass, field
import time
import sys
import os

# 尝试导入高级滑点模型
try:
    # 添加quant_system路径
    sys.path.append('/root/.openclaw/workspace/quant_system')
    from slippage.liquidity_impact_model import (
        AdvancedSlippageModel, 
        BacktestLiquidityEnforcer,
        StockLiquidityProfile,
        MarketRegime,
        VolumePercentageFilter,
        OrderBookSimulator
    )
    ADVANCED_SLIPPAGE_AVAILABLE = True
except ImportError as e:
    print(f"警告: 高级滑点模型不可用: {e}")
    ADVANCED_SLIPPAGE_AVAILABLE = False
    AdvancedSlippageModel = None
    BacktestLiquidityEnforcer = None
    StockLiquidityProfile = None
    MarketRegime = None
    VolumePercentageFilter = None
    OrderBookSimulator = None

@dataclass
class BacktestConfig:
    """回测配置"""
    initial_capital: float = 1000000.0  # 初始资金
    commission_rate: float = 0.001      # 佣金费率 0.1%
    slippage_rate: float = 0.002        # 滑点费率 0.2%（基础固定滑点）
    min_trade_value: float = 1000.0     # 最小交易金额
    max_position_pct: float = 0.1       # 单票最大仓位比例
    stop_loss_pct: float = 0.10         # 止损比例 10%
    take_profit_pct: float = 0.20       # 止盈比例 20%
    
    # 高级滑点模型配置（用户建议）
    use_advanced_slippage: bool = False          # 使用高级滑点模型
    adv_threshold: float = 3000.0                # ADV过滤阈值（万元），默认3000万
    market_cap_threshold: float = 30.0           # 流通市值过滤阈值（亿元），默认30亿
    enforce_tplus1: bool = True                  # 强制执行T+1约束
    enforce_limit_up_down: bool = True           # 强制执行涨跌停板过滤
    filter_low_liquidity: bool = True            # 过滤低流动性股票
    volume_percentage_limit: float = 0.05        # 成交量占比限制（默认5%），单日成交量不得超过该股当日总成交的百分比
    
    # 市场状态
    market_regime: str = 'normal'                # 市场状态：normal, volatile, crash, bull, bear
    
@dataclass
class StockLiquidityData:
    """股票流动性数据（用于高级滑点模型）"""
    symbol: str
    adv_20d: float = 0.0              # 过去20日平均日成交额（万元）
    market_cap: float = 0.0           # 流通市值（亿元）
    is_st: bool = False               # 是否为ST股票
    daily_turnover: float = 0.0       # 日换手率
    
@dataclass
class TradeRecord:
    """交易记录"""
    date: pd.Timestamp
    symbol: str
    action: str          # BUY/SELL
    shares: int
    price: float
    value: float
    commission: float
    slippage: float
    position_before: int
    position_after: int
    cash_before: float
    cash_after: float
    metadata: Optional[Dict[str, Any]] = None
    
@dataclass
class BacktestResult:
    """回测结果"""
    symbol: str
    total_return: float          # 总收益率
    annual_return: float         # 年化收益率
    sharpe_ratio: float          # 夏普比率
    sortino_ratio: float         # 索提诺比率
    max_drawdown: float          # 最大回撤
    win_rate: float              # 胜率
    profit_factor: float         # 盈亏比
    total_trades: int            # 总交易次数
    profitable_trades: int       # 盈利交易次数
    avg_profit: float            # 平均盈利
    avg_loss: float              # 平均亏损
    trade_records: List[TradeRecord]
    portfolio_values: pd.Series
    dates: pd.DatetimeIndex

class VectorizedBacktester:
    """向量化回测器"""
    
    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()
        self.results_cache = {}
        
        # 初始化高级滑点模型（如果可用且配置启用）
        self.liquidity_enforcer = None
        
        # 初始化成交量占比过滤器（如果可用）
        self.volume_filter = None
        if VolumePercentageFilter is not None:
            try:
                self.volume_filter = VolumePercentageFilter(
                    max_percentage=self.config.volume_percentage_limit
                )
                print(f"✓ 成交量占比限制已启用 (最大{self.config.volume_percentage_limit:.0%})")
            except Exception as e:
                print(f"⚠️ 成交量占比过滤器初始化失败: {e}")
                self.volume_filter = None
        
        if (self.config.use_advanced_slippage and 
            ADVANCED_SLIPPAGE_AVAILABLE and 
            BacktestLiquidityEnforcer is not None):
            try:
                self.liquidity_enforcer = BacktestLiquidityEnforcer()
                print(f"✓ 高级滑点模型已启用 (ADV阈值: {self.config.adv_threshold}万, 市值阈值: {self.config.market_cap_threshold}亿)")
            except Exception as e:
                print(f"⚠️ 高级滑点模型初始化失败: {e}")
                self.liquidity_enforcer = None
        
    def run_vectorized_backtest(self, 
                               symbol: str,
                               prices: pd.DataFrame,
                               signals: pd.Series,
                               start_date: Optional[pd.Timestamp] = None,
                               end_date: Optional[pd.Timestamp] = None,
                               liquidity_data: Optional[Dict[str, Any]] = None) -> BacktestResult:
        """
        运行向量化回测
        
        Args:
            symbol: 股票代码
            prices: 价格DataFrame，需包含'open', 'high', 'low', 'close', 'volume'列
            signals: 交易信号Series，1=买入，-1=卖出，0=持有
            start_date: 回测开始日期
            end_date: 回测结束日期
            liquidity_data: 流动性数据字典（用于高级滑点模型）
                {
                    'adv_20d': float,      # 过去20日平均日成交额（万元）
                    'market_cap': float,   # 流通市值（亿元）
                    'is_st': bool,         # 是否为ST股票
                    'daily_turnover': float # 日换手率
                }
            
        Returns:
            回测结果
        """
        start_time = time.time()
        
        # 数据预处理
        if start_date is not None:
            mask = prices.index >= start_date
            prices = prices[mask]
            signals = signals[mask]
            
        if end_date is not None:
            mask = prices.index <= end_date
            prices = prices[mask]
            signals = signals[mask]
        
        if prices.empty or signals.empty:
            raise ValueError("价格数据或信号数据为空")
        
        # 对齐数据
        common_idx = prices.index.intersection(signals.index)
        if len(common_idx) == 0:
            raise ValueError("价格数据和信号数据日期不匹配")
        
        prices = prices.loc[common_idx]
        signals = signals.loc[common_idx]
        
        print(f"向量化回测 {symbol}: {len(prices)}个交易日")
        
        # ========== 初始化高级滑点模型（如果启用）==========
        stock_profile = None
        market_regime = None
        
        if (self.config.use_advanced_slippage and 
            self.liquidity_enforcer is not None and 
            liquidity_data is not None):
            
            try:
                # 初始化市场状态
                if hasattr(MarketRegime, self.config.market_regime.upper()):
                    market_regime = getattr(MarketRegime, self.config.market_regime.upper())
                else:
                    market_regime = MarketRegime.NORMAL
                
                # 初始化股票流动性画像
                adv_20d = liquidity_data.get('adv_20d', 0.0)
                market_cap = liquidity_data.get('market_cap', 0.0)
                is_st = liquidity_data.get('is_st', False)
                
                # 获取当前价格（用于计算涨跌停价）
                current_price = closes[0] if len(closes) > 0 else 0.0
                limit_up_price = current_price * 1.1 if current_price > 0 else 0.0
                limit_down_price = current_price * 0.9 if current_price > 0 else 0.0
                
                stock_profile = self.liquidity_enforcer.slippage_model.create_stock_profile(
                    symbol=symbol,
                    adv_20d=adv_20d,
                    market_cap=market_cap,
                    is_st=is_st,
                    price=current_price,
                    limit_up_price=limit_up_price,
                    limit_down_price=limit_down_price
                )
                
                print(f"  高级滑点模型已启用: ADV={adv_20d:.0f}万, 市值={market_cap:.1f}亿, ST={is_st}")
                
                # 检查是否为低流动性股票
                if stock_profile.is_low_liquidity(
                    adv_threshold=self.config.adv_threshold,
                    market_cap_threshold=self.config.market_cap_threshold
                ):
                    print(f"  ⚠️  警告: {symbol}为低流动性股票，回测结果可能虚高")
                    
            except Exception as e:
                print(f"  ⚠️  高级滑点模型初始化失败: {e}")
                stock_profile = None
        
        # ========== 核心向量化计算 ==========
        
        # 1. 准备数据数组（提高访问速度）
        dates = prices.index
        closes = prices['close'].values
        opens = prices['open'].values
        
        # 信号数组
        signal_vals = signals.values
        
        # 2. 向量化计算仓位变化点
        # 信号变化点：从0变1（买入），从1变-1（卖出）
        signal_changes = np.zeros_like(signal_vals, dtype=int)
        signal_changes[1:] = signal_vals[1:] - signal_vals[:-1]
        
        # 买入信号：信号从<=0变>0
        buy_signals = (signal_vals > 0) & (signal_changes > 0)
        
        # 卖出信号：信号从>=0变<0
        sell_signals = (signal_vals < 0) & (signal_changes < 0)
        
        # 3. 向量化计算交易
        n_days = len(closes)
        
        # 初始化数组
        positions = np.zeros(n_days, dtype=int)      # 每日持仓
        cash = np.zeros(n_days, dtype=float)         # 每日现金
        portfolio_values = np.zeros(n_days, dtype=float)  # 每日组合价值
        
        # 交易记录
        trade_records = []
        
        # 初始状态
        cash[0] = self.config.initial_capital
        portfolio_values[0] = self.config.initial_capital
        
        # 逐日向量化计算（但比原始循环优化）
        for i in range(1, n_days):
            # 继承前一日的状态
            positions[i] = positions[i-1]
            cash[i] = cash[i-1]
            
            current_price = closes[i]
            open_price = opens[i]
            
            # 买入信号
            if buy_signals[i] and positions[i] == 0 and cash[i] > self.config.min_trade_value:
                # 计算可买数量（考虑最大仓位限制）
                max_trade_value = cash[i] * self.config.max_position_pct
                available_cash = min(cash[i], max_trade_value)
                
                # 计算实际买入价格（考虑滑点）
                if self.config.use_advanced_slippage and stock_profile is not None:
                    # 使用高级滑点模型计算冲击成本
                    trade_value_estimate = available_cash * 0.8  # 估算交易金额（假设使用80%可用资金）
                    
                    # 确定交易时间（简化：根据日期时间判断）
                    trade_time = 'midday'  # 默认盘中
                    
                    # 计算冲击成本
                    slippage_result = self.liquidity_enforcer.slippage_model.calculate_slippage(
                        stock_profile=stock_profile,
                        trade_side='buy',
                        trade_value=trade_value_estimate,
                        trade_time=trade_time,
                        market_regime=market_regime
                    )
                    
                    impact_pct = slippage_result['impact_pct']
                    buy_price = open_price * (1 + impact_pct)
                    advanced_slippage_used = True
                else:
                    # 使用固定滑点率
                    buy_price = open_price * (1 + self.config.slippage_rate)
                    impact_pct = self.config.slippage_rate
                    advanced_slippage_used = False
                
                # 计算可买股数（考虑冲击成本和佣金）
                shares = int(available_cash / (buy_price * (1 + self.config.commission_rate)))
                
                if shares > 0:
                    # 计算成本
                    trade_value = shares * buy_price
                    commission = trade_value * self.config.commission_rate
                    total_cost = trade_value + commission
                    
                    # 更新状态
                    positions[i] = shares
                    cash[i] -= total_cost
                    
                    # 准备metadata
                    metadata = {}
                    if advanced_slippage_used and stock_profile is not None:
                        metadata.update({
                            'advanced_slippage': True,
                            'impact_pct': impact_pct,
                            'impact_bps': impact_pct * 10000,
                            'bucket_id': stock_profile.bucket_id,
                            'adv_20d': stock_profile.adv_20d,
                            'market_cap': stock_profile.market_cap,
                            'is_st': stock_profile.is_st,
                            'is_low_liquidity': stock_profile.is_low_liquidity(
                                self.config.adv_threshold, 
                                self.config.market_cap_threshold
                            )
                        })
                    else:
                        metadata.update({
                            'advanced_slippage': False,
                            'slippage_rate': self.config.slippage_rate
                        })
                    
                # 记录交易
                # 应用成交量占比过滤器（如果启用）
    if self.volume_filter is not None:
        daily_volume = prices.iloc[i]['volume']
        if daily_volume > 0:
            liquidity_check = self.volume_filter.check_order_size(
                order_volume=shares,
                daily_volume=daily_volume,
                symbol=symbol
            )
            if not liquidity_check['allowed']:
                # 订单超过流动性限制，调整订单大小
                adjustment = self.volume_filter.adjust_order_for_liquidity(
                    order_volume=shares,
                    daily_volume=daily_volume
                )
                if adjustment['passes_check']:
                    shares = int(adjustment['adjusted_volume'])
                    print(f"流动性调整: {adjustment['reason']}")
                else:
                    # 无法调整，跳过此交易
                    print(f"流动性限制: 订单被拒绝，{liquidity_check['message']}")
                    continue
    
    trade = TradeRecord(
        date=dates[i],
        symbol=symbol,
        action='BUY',
        shares=shares,
        price=buy_price,
        value=trade_value,
        commission=commission,
        slippage=open_price * impact_pct,  # 使用实际冲击成本
        position_before=0,
        position_after=shares,
        cash_before=cash[i-1],
        cash_after=cash[i],
        metadata=metadata
    )
    trade_records.append(trade)
            
            # 卖出信号
            elif sell_signals[i] and positions[i] > 0:
                # 计算实际卖出价格（考虑滑点）
                if self.config.use_advanced_slippage and stock_profile is not None:
                    # 使用高级滑点模型计算冲击成本
                    trade_value_estimate = positions[i] * open_price  # 估算交易金额
                    
                    # 确定交易时间（简化：根据日期时间判断）
                    trade_time = 'midday'  # 默认盘中
                    
                    # 计算冲击成本（卖出冲击通常更大）
                    slippage_result = self.liquidity_enforcer.slippage_model.calculate_slippage(
                        stock_profile=stock_profile,
                        trade_side='sell',
                        trade_value=trade_value_estimate,
                        trade_time=trade_time,
                        market_regime=market_regime
                    )
                    
                    impact_pct = slippage_result['impact_pct']
                    sell_price = open_price * (1 - impact_pct)
                    sell_advanced_slippage_used = True
                else:
                    # 使用固定滑点率
                    sell_price = open_price * (1 - self.config.slippage_rate)
                    impact_pct = self.config.slippage_rate
                    sell_advanced_slippage_used = False
                
                # 计算收入
                trade_value = positions[i] * sell_price
                commission = trade_value * self.config.commission_rate
                net_proceeds = trade_value - commission
                
                # 计算盈亏
                entry_price = self._get_entry_price(trade_records, positions[i])
                profit_pct = (sell_price - entry_price) / entry_price if entry_price > 0 else 0
                
                # 检查止损/止盈
                should_sell = True
                if profit_pct < -self.config.stop_loss_pct:
                    reason = "止损"
                elif profit_pct > self.config.take_profit_pct:
                    reason = "止盈"
                else:
                    reason = "信号卖出"
                
                if should_sell:
                    # 更新状态
                    cash[i] += net_proceeds
                    positions[i] = 0
                    
                    # 准备metadata
                    sell_metadata = {'profit_pct': profit_pct, 'reason': reason}
                    if sell_advanced_slippage_used and stock_profile is not None:
                        sell_metadata.update({
                            'advanced_slippage': True,
                            'impact_pct': impact_pct,
                            'impact_bps': impact_pct * 10000,
                            'bucket_id': stock_profile.bucket_id,
                            'adv_20d': stock_profile.adv_20d,
                            'market_cap': stock_profile.market_cap,
                            'is_st': stock_profile.is_st,
                            'is_low_liquidity': stock_profile.is_low_liquidity(
                                self.config.adv_threshold, 
                                self.config.market_cap_threshold
                            )
                        })
                    else:
                        sell_metadata.update({
                            'advanced_slippage': False,
                            'slippage_rate': self.config.slippage_rate
                        })
                    
                # 记录交易
                # 应用成交量占比过滤器（如果启用）
    if self.volume_filter is not None:
        daily_volume = prices.iloc[i]['volume']
        if daily_volume > 0:
            liquidity_check = self.volume_filter.check_order_size(
                order_volume=shares,
                daily_volume=daily_volume,
                symbol=symbol
            )
            if not liquidity_check['allowed']:
                # 订单超过流动性限制，调整订单大小
                adjustment = self.volume_filter.adjust_order_for_liquidity(
                    order_volume=shares,
                    daily_volume=daily_volume
                )
                if adjustment['passes_check']:
                    shares = int(adjustment['adjusted_volume'])
                    print(f"流动性调整: {adjustment['reason']}")
                else:
                    # 无法调整，跳过此交易
                    print(f"流动性限制: 订单被拒绝，{liquidity_check['message']}")
                    continue
    
    trade = TradeRecord(
        date=dates[i],
        symbol=symbol,
        action='SELL',
        shares=shares,
        price=sell_price,
        value=trade_value,
        commission=commission,
        slippage=open_price * impact_pct,  # 使用实际冲击成本
        position_before=position_before,
        position_after=0,
        cash_before=cash[i-1],
        cash_after=cash[i],
        metadata=metadata
    )
    trade_records.append(trade)
            
            # 计算当日组合价值
            position_value = positions[i] * current_price
            portfolio_values[i] = cash[i] + position_value
        
        # 4. 计算绩效指标
        result = self._calculate_performance_metrics(
            symbol=symbol,
            dates=dates,
            portfolio_values=portfolio_values,
            trade_records=trade_records
        )
        
        elapsed_time = time.time() - start_time
        print(f"  完成时间: {elapsed_time:.2f}秒")
        print(f"  总收益: {result.total_return*100:.1f}%")
        print(f"  交易次数: {result.total_trades}")
        
        return result
    
    def _get_entry_price(self, trade_records: List[TradeRecord], current_shares: int) -> float:
        """计算持仓成本价（先进先出法）"""
        if not trade_records:
            return 0.0
        
        # 收集所有买入交易
        buy_trades = [t for t in trade_records if t.action == 'BUY']
        
        # 先进先出计算平均成本
        remaining_shares = current_shares
        total_cost = 0.0
        
        for trade in reversed(buy_trades):  # 从最近的买入开始
            if remaining_shares <= 0:
                break
            
            shares_to_use = min(trade.shares, remaining_shares)
            trade_cost = trade.price * shares_to_use
            total_cost += trade_cost
            remaining_shares -= shares_to_use
        
        if current_shares > 0:
            return total_cost / current_shares
        else:
            return 0.0
    
    def _calculate_performance_metrics(self,
                                      symbol: str,
                                      dates: pd.DatetimeIndex,
                                      portfolio_values: np.ndarray,
                                      trade_records: List[TradeRecord]) -> BacktestResult:
        """计算绩效指标"""
        
        # 日收益率
        daily_returns = np.zeros_like(portfolio_values, dtype=float)
        daily_returns[1:] = portfolio_values[1:] / portfolio_values[:-1] - 1
        
        # 总收益率
        total_return = portfolio_values[-1] / portfolio_values[0] - 1
        
        # 年化收益率
        n_days = len(dates)
        if n_days > 1:
            years = n_days / 252.0  # 交易日转年
            annual_return = (1 + total_return) ** (1 / years) - 1
        else:
            annual_return = 0.0
        
        # 夏普比率（无风险利率3%）
        if len(daily_returns) > 1:
            excess_returns = daily_returns - 0.03/252
            sharpe_ratio = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252)
        else:
            sharpe_ratio = 0.0
        
        # 索提诺比率（只考虑下行风险）
        if len(daily_returns) > 1:
            downside_returns = daily_returns[daily_returns < 0]
            if len(downside_returns) > 0:
                downside_std = np.std(downside_returns)
                sortino_ratio = np.mean(excess_returns) / downside_std * np.sqrt(252) if downside_std > 0 else 0.0
            else:
                sortino_ratio = float('inf') if np.mean(excess_returns) > 0 else 0.0
        else:
            sortino_ratio = 0.0
        
        # 最大回撤
        cum_returns = (1 + daily_returns).cumprod()
        running_max = np.maximum.accumulate(cum_returns)
        drawdowns = (cum_returns - running_max) / running_max
        max_drawdown = np.min(drawdowns)
        
        # 交易统计
        sell_trades = [t for t in trade_records if t.action == 'SELL']
        total_trades = len(sell_trades)
        
        profitable_trades = 0
        profit_sum = 0.0
        loss_sum = 0.0
        
        for trade in sell_trades:
            if hasattr(trade, 'metadata') and 'profit_pct' in trade.metadata:
                profit_pct = trade.metadata['profit_pct']
                if profit_pct > 0:
                    profitable_trades += 1
                    profit_sum += profit_pct
                else:
                    loss_sum += abs(profit_pct)
        
        win_rate = profitable_trades / total_trades if total_trades > 0 else 0.0
        
        # 盈亏比
        if loss_sum > 0:
            profit_factor = profit_sum / loss_sum
        else:
            profit_factor = float('inf') if profit_sum > 0 else 0.0
        
        # 平均盈利/亏损
        avg_profit = profit_sum / profitable_trades if profitable_trades > 0 else 0.0
        avg_loss = loss_sum / (total_trades - profitable_trades) if (total_trades - profitable_trades) > 0 else 0.0
        
        return BacktestResult(
            symbol=symbol,
            total_return=float(total_return),
            annual_return=float(annual_return),
            sharpe_ratio=float(sharpe_ratio),
            sortino_ratio=float(sortino_ratio),
            max_drawdown=float(max_drawdown),
            win_rate=float(win_rate),
            profit_factor=float(profit_factor),
            total_trades=total_trades,
            profitable_trades=profitable_trades,
            avg_profit=float(avg_profit),
            avg_loss=float(avg_loss),
            trade_records=trade_records,
            portfolio_values=pd.Series(portfolio_values, index=dates),
            dates=dates
        )
    
    def run_batch_backtest(self,
                          symbols: List[str],
                          prices_dict: Dict[str, pd.DataFrame],
                          signals_dict: Dict[str, pd.Series],
                          parallel: bool = True,
                          max_workers: int = 4) -> Dict[str, BacktestResult]:
        """
        批量回测多支股票
        
        Args:
            symbols: 股票代码列表
            prices_dict: 价格数据字典 {symbol: prices_df}
            signals_dict: 信号数据字典 {symbol: signals_series}
            parallel: 是否并行计算
            max_workers: 最大并行数
            
        Returns:
            回测结果字典
        """
        results = {}
        
        if parallel:
            import concurrent.futures
            
            print(f"并行回测 {len(symbols)} 支股票，使用 {max_workers} 个进程")
            
            with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
                future_to_symbol = {}
                
                for symbol in symbols:
                    if symbol in prices_dict and symbol in signals_dict:
                        future = executor.submit(
                            self.run_vectorized_backtest,
                            symbol,
                            prices_dict[symbol],
                            signals_dict[symbol]
                        )
                        future_to_symbol[future] = symbol
                
                for future in concurrent.futures.as_completed(future_to_symbol):
                    symbol = future_to_symbol[future]
                    try:
                        result = future.result()
                        results[symbol] = result
                        print(f"  {symbol}: 完成，收益{result.total_return*100:.1f}%")
                    except Exception as e:
                        print(f"  {symbol}: 失败 - {e}")
                        results[symbol] = None
        else:
            # 串行回测
            for symbol in symbols:
                if symbol in prices_dict and symbol in signals_dict:
                    try:
                        result = self.run_vectorized_backtest(
                            symbol,
                            prices_dict[symbol],
                            signals_dict[symbol]
                        )
                        results[symbol] = result
                    except Exception as e:
                        print(f"  {symbol}: 失败 - {e}")
                        results[symbol] = None
        
        return results

# ========== 性能测试工具 ==========

class BacktestBenchmark:
    """回测性能基准测试"""
    
    @staticmethod
    def generate_test_data(n_days: int = 1000, n_symbols: int = 10) -> Tuple[Dict, Dict]:
        """生成测试数据"""
        prices_dict = {}
        signals_dict = {}
        
        base_date = pd.Timestamp('2020-01-01')
        dates = pd.date_range(start=base_date, periods=n_days, freq='B')
        
        for i in range(n_symbols):
            symbol = f"TEST{i:03d}"
            
            # 生成随机价格
            np.random.seed(i)
            base_price = 50 + np.random.randn() * 10
            returns = np.random.randn(n_days) * 0.02
            prices = base_price * (1 + np.cumsum(returns))
            prices = np.maximum(prices, 1.0)
            
            df = pd.DataFrame({
                'open': prices * 0.99,
                'high': prices * 1.02,
                'low': prices * 0.98,
                'close': prices,
                'volume': np.random.randint(1000000, 10000000, n_days)
            }, index=dates)
            
            # 生成随机信号（模拟策略）
            np.random.seed(i + 1000)
            signals = np.random.choice([-1, 0, 1], size=n_days, p=[0.1, 0.8, 0.1])
            signals_series = pd.Series(signals, index=dates)
            
            prices_dict[symbol] = df
            signals_dict[symbol] = signals_series
        
        return prices_dict, signals_dict
    
    @staticmethod
    def benchmark_single_stock():
        """单支股票回测基准测试"""
        print("单支股票回测基准测试")
        print("=" * 60)
        
        # 生成测试数据
        prices_dict, signals_dict = BacktestBenchmark.generate_test_data(
            n_days=1000, n_symbols=1
        )
        
        symbol = "TEST000"
        backtester = VectorizedBacktester()
        
        # 测试向量化回测
        start_time = time.time()
        result = backtester.run_vectorized_backtest(
            symbol,
            prices_dict[symbol],
            signals_dict[symbol]
        )
        vectorized_time = time.time() - start_time
        
        print(f"向量化回测时间: {vectorized_time:.3f}秒")
        print(f"日收益率计算速度: {1000/vectorized_time:.0f} 天/秒")
        
        return vectorized_time
    
    @staticmethod
    def benchmark_batch_stocks(n_symbols: int = 10, parallel: bool = True):
        """批量回测基准测试"""
        print(f"批量回测基准测试 ({n_symbols}支股票, {'并行' if parallel else '串行'})")
        print("=" * 60)
        
        # 生成测试数据
        prices_dict, signals_dict = BacktestBenchmark.generate_test_data(
            n_days=756, n_symbols=n_symbols  # 3年交易日 ≈ 756天
        )
        
        symbols = list(prices_dict.keys())
        backtester = VectorizedBacktester()
        
        # 测试批量回测
        start_time = time.time()
        results = backtester.run_batch_backtest(
            symbols,
            prices_dict,
            signals_dict,
            parallel=parallel,
            max_workers=4
        )
        total_time = time.time() - start_time
        
        successful = sum(1 for r in results.values() if r is not None)
        avg_time_per_stock = total_time / successful if successful > 0 else 0
        
        print(f"总回测时间: {total_time:.3f}秒")
        print(f"成功回测股票数: {successful}/{n_symbols}")
        print(f"平均每支股票时间: {avg_time_per_stock:.3f}秒")
        print(f"总处理速度: {n_symbols*756/total_time:.0f} 天/秒")
        
        # 检查是否达到5分钟目标
        if total_time <= 300:  # 5分钟 = 300秒
            print("✅ 达到5分钟回测目标！")
        else:
            print(f"⚠️ 未达到5分钟目标，超出{total_time-300:.1f}秒")
        
        return total_time

# ========== 示例使用 ==========

def example_usage():
    """示例使用方法"""
    print("向量化回测引擎示例")
    print("=" * 60)
    
    # 生成测试数据
    n_symbols = 3
    n_days = 500  # 约2年数据
    
    print(f"生成 {n_symbols} 支股票 {n_days} 天测试数据...")
    prices_dict, signals_dict = BacktestBenchmark.generate_test_data(
        n_days=n_days, n_symbols=n_symbols
    )
    
    # 创建回测器
    config = BacktestConfig(
        initial_capital=1000000.0,
        commission_rate=0.001,
        slippage_rate=0.002,
        max_position_pct=0.2
    )
    backtester = VectorizedBacktester(config)
    
    # 单支股票回测
    print("\n1. 单支股票回测测试:")
    symbol = list(prices_dict.keys())[0]
    result = backtester.run_vectorized_backtest(
        symbol, prices_dict[symbol], signals_dict[symbol]
    )
    
    print(f"\n绩效指标:")
    print(f"  总收益: {result.total_return*100:.2f}%")
    print(f"  年化收益: {result.annual_return*100:.2f}%")
    print(f"  夏普比率: {result.sharpe_ratio:.2f}")
    print(f"  最大回撤: {result.max_drawdown*100:.2f}%")
    print(f"  胜率: {result.win_rate*100:.1f}%")
    print(f"  交易次数: {result.total_trades}")
    
    # 批量回测
    print("\n2. 批量回测测试 (3支股票):")
    total_time = BacktestBenchmark.benchmark_batch_stocks(
        n_symbols=3, parallel=True
    )
    
    # 性能基准测试
    print("\n3. 性能基准测试:")
    print("   目标: 10支股票3年数据(7560天)在5分钟(300秒)内完成")
    print("   当前速度基准: ", end="")
    
    single_time = BacktestBenchmark.benchmark_single_stock()
    estimated_total = single_time * 10 * (756/1000)  # 估算10支股票3年数据时间
    speedup_needed = estimated_total / 300
    
    print(f"\n估算10支股票3年回测时间: {estimated_total:.1f}秒 ({estimated_total/60:.1f}分钟)")
    print(f"需要加速倍数: {speedup_needed:.1f}x")
    
    if speedup_needed > 1:
        print(f"💡 建议优化: 使用并行计算可加速{min(4, speedup_needed):.1f}x")
        print(f"💡 建议优化: 数据预处理和向量化可再加速{speedup_needed/4:.1f}x")
    else:
        print("✅ 当前速度已满足5分钟目标！")

if __name__ == "__main__":
    example_usage()