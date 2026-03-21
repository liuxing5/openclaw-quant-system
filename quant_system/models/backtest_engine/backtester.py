"""
简化版回测引擎
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Any


class BacktestEngine:
    """简化回测引擎"""
    
    def __init__(self, initial_capital: float = 1000000.0):
        self.initial_capital = initial_capital
    
    def run_simple_backtest(self, 
                           prices: pd.DataFrame,
                           signals: pd.Series,
                           commission: float = 0.001,
                           slippage: float = 0.002
                          ) -> Dict[str, Any]:
        """运行简化回测"""
        
        # 对齐数据
        common_idx = prices.index.intersection(signals.index)
        if len(common_idx) == 0:
            return {'error': '数据不匹配'}
        
        prices = prices.loc[common_idx]
        signals = signals.loc[common_idx]
        
        # 初始化
        capital = self.initial_capital
        cash = capital
        position = 0  # 持股数量
        entry_price = 0
        
        portfolio_values = []
        trades = []
        
        # 逐日模拟
        prev_signal = 0
        for date in common_idx:
            price = prices.loc[date, 'close'] if 'close' in prices.columns else prices.loc[date, 0]
            signal = signals.loc[date]
            
            # 信号变化：买入或卖出
            if signal == 1 and prev_signal != 1:  # 买入信号
                if position == 0 and cash > 0:
                    # 计算可买数量（全仓买入）
                    buy_price = price * (1 + slippage)  # 考虑滑点
                    shares = int(cash * (1 - commission) / buy_price)
                    
                    if shares > 0:
                        cost = shares * buy_price
                        cash -= cost
                        position = shares
                        entry_price = buy_price
                        
                        trades.append({
                            'date': date,
                            'action': 'BUY',
                            'shares': shares,
                            'price': buy_price,
                            'value': cost
                        })
            
            elif signal == -1 and prev_signal != -1:  # 卖出信号
                if position > 0:
                    sell_price = price * (1 - slippage)  # 考虑滑点
                    value = position * sell_price
                    cash += value * (1 - commission)
                    
                    return_pct = (sell_price - entry_price) / entry_price if entry_price > 0 else 0
                    
                    trades.append({
                        'date': date,
                        'action': 'SELL',
                        'shares': position,
                        'price': sell_price,
                        'value': value,
                        'return_pct': return_pct,
                        'entry_price': entry_price
                    })
                    
                    position = 0
                    entry_price = 0
            
            # 计算当日组合价值
            position_value = position * price
            portfolio_value = cash + position_value
            portfolio_values.append(portfolio_value)
            
            prev_signal = signal
        
        # 最终平仓
        if position > 0:
            last_date = common_idx[-1]
            last_price = prices.iloc[-1, 0] if len(prices) > 0 else price
            value = position * last_price
            cash += value * (1 - commission)
            
            return_pct = (last_price - entry_price) / entry_price if entry_price > 0 else 0
            
            trades.append({
                'date': last_date,
                'action': 'SELL',
                'shares': position,
                'price': last_price,
                'value': value,
                'return_pct': return_pct,
                'entry_price': entry_price,
                'reason': 'end_of_period'
            })
            
            position = 0
        
        # 计算绩效指标
        portfolio_series = pd.Series(portfolio_values, index=common_idx[:len(portfolio_values)])
        returns = portfolio_series.pct_change().fillna(0)
        
        # 基本指标
        total_return = (portfolio_series.iloc[-1] / self.initial_capital - 1) if len(portfolio_series) > 0 else 0
        
        # 年化收益
        if len(returns) > 0:
            days = len(returns)
            annual_return = (1 + total_return) ** (252 / days) - 1
            
            # 年化波动
            annual_vol = returns.std() * np.sqrt(252)
            
            # 夏普比率（假设无风险利率3%）
            risk_free = 0.03
            sharpe = (annual_return - risk_free) / annual_vol if annual_vol > 0 else 0
            
            # 最大回撤
            cum_returns = (1 + returns).cumprod()
            running_max = cum_returns.expanding().max()
            drawdown = (cum_returns - running_max) / running_max
            max_dd = drawdown.min()
            
            # 胜率
            win_rate = (returns > 0).mean()
            
            # 盈亏比
            winning = returns[returns > 0]
            losing = returns[returns < 0]
            avg_win = winning.mean() if len(winning) > 0 else 0
            avg_loss = abs(losing.mean()) if len(losing) > 0 else 0
            profit_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
        else:
            annual_return = 0
            annual_vol = 0
            sharpe = 0
            max_dd = 0
            win_rate = 0
            profit_ratio = 0
        
        return {
            'portfolio_values': portfolio_series,
            'returns': returns,
            'trades': trades,
            'metrics': {
                'initial_capital': self.initial_capital,
                'final_value': portfolio_series.iloc[-1] if len(portfolio_series) > 0 else self.initial_capital,
                'total_return': float(total_return),
                'annual_return': float(annual_return),
                'annual_volatility': float(annual_vol),
                'sharpe_ratio': float(sharpe),
                'max_drawdown': float(max_dd),
                'win_rate': float(win_rate),
                'profit_loss_ratio': float(profit_ratio),
                'num_trades': len([t for t in trades if t['action'] == 'SELL']),
                'avg_holding_days': 5  # 简化
            },
            'parameters': {
                'commission': commission,
                'slippage': slippage
            }
        }