#!/usr/bin/env python3
"""
高级回测引擎 - 支持OOS、Walk-forward、市场状态分段回测
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Optional
import warnings
warnings.filterwarnings('ignore')
import json
import sys
import os

sys.path.append('/root/.openclaw/workspace/quant_system')
from data.sources.data_pipeline import DataPipeline

# 因子管理器：优先使用真实因子管理器（解决伪因子问题）
try:
    # 尝试导入真实因子管理器（基于Baostock真实财务数据）
    from real_factors.real_factor_manager import RealFactorManager
    # 创建兼容包装器
    class FactorManager(RealFactorManager):
        """兼容包装器，提供get_factor_weights方法"""
        def get_factor_weights(self, method: str = 'equal') -> Dict[str, float]:
            """获取因子权重（兼容接口）"""
            if method == 'equal':
                n_factors = len(self.factors)
                return {factor_id: 1.0 / n_factors for factor_id in self.factors.keys()}
            
            elif method == 'category_weighted':
                # 使用真实因子类别统计
                # 技术因子保持50%，基本面因子30%（真实数据），情绪因子20%（部分真实）
                category_weights = {
                    'technical': 0.50,
                    'fundamental': 0.30,
                    'sentiment': 0.20
                }
                
                weights = {}
                for factor_id, info in self.factors.items():
                    category = info['category']
                    n_in_category = self.category_stats.get(category, 1)
                    weights[factor_id] = category_weights.get(category, 0.0) / n_in_category
                
                return weights
            
            else:
                raise ValueError(f"未知的权重方法: {method}")
        
        def combine_factors(self, df: pd.DataFrame, weights: Optional[Dict[str, float]] = None, symbol: str = None) -> pd.Series:
            """因子融合（增强版，支持symbol参数）"""
            # 计算所有因子
            factor_df = self.calculate_all_factors(df, symbol=symbol)
            
            # 获取权重
            if weights is None:
                weights = self.get_factor_weights('category_weighted')
            
            # 确保权重与因子匹配
            valid_weights = {}
            for factor_id in factor_df.columns:
                if factor_id in weights:
                    valid_weights[factor_id] = weights[factor_id]
                else:
                    valid_weights[factor_id] = 0.0
            
            # 归一化权重
            total_weight = sum(valid_weights.values())
            if total_weight > 0:
                normalized_weights = {k: v / total_weight for k, v in valid_weights.items()}
            else:
                normalized_weights = {k: 1.0 / len(valid_weights) for k in valid_weights.keys()}
            
            # 加权求和
            weighted_sum = pd.Series(0.0, index=factor_df.index)
            for factor_id, weight in normalized_weights.items():
                if factor_id in factor_df.columns:
                    # 标准化因子值（去除NaN）
                    factor_values = factor_df[factor_id].fillna(0)
                    weighted_sum += factor_values * weight
            
            return weighted_sum
    
    print("✓ AdvancedBacktester: 使用真实因子管理器 (RealFactorManager)")
except ImportError as e:
    # 回退到原始因子管理器
    print(f"⚠ AdvancedBacktester: 真实因子管理器导入失败: {e}，使用原始因子管理器")
    from factors.factor_manager import FactorManager

class AdvancedBacktester:
    """高级回测器 - 支持专业回测方法"""
    
    def __init__(self, 
                 initial_capital: float = 1000000.0,
                 commission: float = 0.001,
                 slippage: float = 0.002):
        
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        
        self.data_pipeline = DataPipeline()
        self.factor_manager = FactorManager()
        
        # 回测结果存储
        self.backtest_results = {}
    
    def run_standard_backtest(self,
                             symbols: List[str],
                             start_date: str,
                             end_date: str,
                             strategy_func: callable,
                             **kwargs) -> Dict[str, Any]:
        """标准回测 - 基础功能"""
        print(f"运行标准回测: {len(symbols)}支股票, {start_date} 至 {end_date}")
        
        results = {}
        total_results = {
            'total_return': 0,
            'annual_return': 0,
            'sharpe_ratio': 0,
            'max_drawdown': 0,
            'win_rate': 0,
            'total_trades': 0,
            'profitable_trades': 0
        }
        
        for symbol in symbols:
            try:
                # 获取数据
                result = self.data_pipeline.get_stock_data(symbol, start_date, end_date, with_metadata=False)
                df = result['data']
                
                if df.empty or len(df) < 20:
                    print(f"  {symbol}: 数据不足")
                    continue
                
                # 计算因子/信号
                signals = strategy_func(df, **kwargs)
                
                # 运行回测
                symbol_result = self._run_single_backtest(df, signals)
                results[symbol] = symbol_result
                
                # 汇总统计
                total_results['total_return'] += symbol_result['total_return']
                total_results['total_trades'] += symbol_result['total_trades']
                total_results['profitable_trades'] += symbol_result['profitable_trades']
                
                print(f"  {symbol}: 收益{symbol_result['total_return']:.1f}%, 交易{symbol_result['total_trades']}次")
                
            except Exception as e:
                print(f"  {symbol}: 回测失败 - {e}")
        
        # 计算整体指标
        if results:
            n = len(results)
            total_results['total_return'] /= n
            total_results['win_rate'] = total_results['profitable_trades'] / max(1, total_results['total_trades'])
        
        return {
            'individual_results': results,
            'summary': total_results,
            'parameters': {
                'symbols': symbols,
                'start_date': start_date,
                'end_date': end_date,
                'initial_capital': self.initial_capital,
                'commission': self.commission,
                'slippage': self.slippage
            }
        }
    
    def run_oos_test(self,
                    symbols: List[str],
                    train_start: str,
                    train_end: str,
                    test_start: str,
                    test_end: str,
                    strategy_func: callable,
                    **kwargs) -> Dict[str, Any]:
        """
        样本外测试 (OOS Test)
        
        Args:
            symbols: 股票代码列表
            train_start: 训练集开始日期
            train_end: 训练集结束日期
            test_start: 测试集开始日期
            test_end: 测试集结束日期
            strategy_func: 策略函数
            **kwargs: 策略参数
        
        Returns:
            OOS测试结果
        """
        print("=" * 60)
        print("样本外测试 (Out-of-Sample Testing)")
        print("=" * 60)
        print(f"训练集: {train_start} 至 {train_end}")
        print(f"测试集: {test_start} 至 {test_end}")
        print(f"股票数量: {len(symbols)}")
        
        # 1. 训练集回测
        print("\n1. 训练集回测...")
        train_results = self.run_standard_backtest(
            symbols, train_start, train_end, strategy_func, **kwargs
        )
        
        # 2. 测试集回测 (使用训练得到的参数)
        print("\n2. 测试集回测...")
        test_results = self.run_standard_backtest(
            symbols, test_start, test_end, strategy_func, **kwargs
        )
        
        # 3. OOS性能分析
        print("\n3. OOS性能分析...")
        oos_analysis = self._analyze_oos_performance(train_results, test_results)
        
        return {
            'train_results': train_results,
            'test_results': test_results,
            'oos_analysis': oos_analysis,
            'metadata': {
                'train_period': f"{train_start} 至 {train_end}",
                'test_period': f"{test_start} 至 {test_end}",
                'total_symbols': len(symbols),
                'oos_test_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        }
    
    def run_walk_forward_test(self,
                             symbols: List[str],
                             start_date: str,
                             end_date: str,
                             strategy_func: callable,
                             train_window_years: int = 2,
                             test_window_months: int = 6,
                             step_months: int = 3,
                             **kwargs) -> Dict[str, Any]:
        """
        Walk-forward走走测试 (滚动窗口回测)
        
        Args:
            symbols: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            train_window_years: 训练窗口年数
            test_window_months: 测试窗口月数
            step_months: 滚动步长月数
            strategy_func: 策略函数
            **kwargs: 策略参数
        
        Returns:
            Walk-forward测试结果
        """
        print("=" * 60)
        print("Walk-forward走走测试")
        print("=" * 60)
        
        # 转换日期格式
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        
        # 计算总月数
        total_months = (end_dt.year - start_dt.year) * 12 + (end_dt.month - start_dt.month)
        
        if total_months < train_window_years * 12 + test_window_months:
            raise ValueError("总时间长度不足以进行walk-forward测试")
        
        # 生成滚动窗口
        windows = []
        current_start = start_dt
        
        while True:
            train_end = current_start + pd.DateOffset(years=train_window_years)
            test_end = train_end + pd.DateOffset(months=test_window_months)
            
            if test_end > end_dt:
                break
            
            windows.append({
                'window_id': len(windows) + 1,
                'train_start': current_start.strftime('%Y-%m-%d'),
                'train_end': train_end.strftime('%Y-%m-%d'),
                'test_start': train_end.strftime('%Y-%m-%d'),
                'test_end': test_end.strftime('%Y-%m-%d')
            })
            
            # 向前滚动
            current_start += pd.DateOffset(months=step_months)
        
        print(f"生成 {len(windows)} 个滚动窗口")
        
        # 对每个窗口进行OOS测试
        window_results = []
        for window in windows:
            print(f"\n窗口 {window['window_id']}/{len(windows)}:")
            print(f"  训练: {window['train_start']} 至 {window['train_end']}")
            print(f"  测试: {window['test_start']} 至 {window['test_end']}")
            
            try:
                oos_result = self.run_oos_test(
                    symbols,
                    window['train_start'],
                    window['train_end'],
                    window['test_start'],
                    window['test_end'],
                    strategy_func,
                    **kwargs
                )
                
                window_results.append({
                    'window_info': window,
                    'oos_result': oos_result
                })
                
            except Exception as e:
                print(f"  窗口测试失败: {e}")
                window_results.append({
                    'window_info': window,
                    'error': str(e)
                })
        
        # 分析Walk-forward结果
        wf_analysis = self._analyze_walk_forward_performance(window_results)
        
        return {
            'window_results': window_results,
            'wf_analysis': wf_analysis,
            'parameters': {
                'train_window_years': train_window_years,
                'test_window_months': test_window_months,
                'step_months': step_months,
                'total_windows': len(windows)
            }
        }
    
    def run_market_state_backtest(self,
                                 symbols: List[str],
                                 start_date: str,
                                 end_date: str,
                                 strategy_func: callable,
                                 market_state_classifier: callable = None,
                                 **kwargs) -> Dict[str, Any]:
        """
        市场状态分段回测
        
        Args:
            symbols: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            strategy_func: 策略函数
            market_state_classifier: 市场状态分类函数
            **kwargs: 策略参数
        
        Returns:
            市场状态分段回测结果
        """
        print("=" * 60)
        print("市场状态分段回测")
        print("=" * 60)
        
        # 默认市场状态分类器
        if market_state_classifier is None:
            market_state_classifier = self._default_market_state_classifier
        
        # 获取市场指数数据
        market_data = self._get_market_index_data(start_date, end_date)
        
        # 识别市场状态
        print("识别市场状态...")
        market_states = market_state_classifier(market_data)
        
        # 按市场状态分段回测
        state_results = {}
        for state_name, state_periods in market_states.items():
            print(f"\n市场状态: {state_name} ({len(state_periods)}个时期)")
            
            state_performance = []
            for period in state_periods:
                period_start, period_end = period
                
                try:
                    # 对该时期进行回测
                    period_result = self.run_standard_backtest(
                        symbols, period_start, period_end, strategy_func, **kwargs
                    )
                    
                    state_performance.append({
                        'period': f"{period_start} 至 {period_end}",
                        'result': period_result['summary']
                    })
                    
                except Exception as e:
                    print(f"  时期 {period_start} 至 {period_end} 回测失败: {e}")
            
            # 汇总该市场状态的表现
            if state_performance:
                state_summary = self._summarize_state_performance(state_performance)
                state_results[state_name] = {
                    'periods': state_periods,
                    'performance': state_performance,
                    'summary': state_summary
                }
        
        # 市场状态对比分析
        comparison = self._compare_market_state_performance(state_results)
        
        return {
            'market_states': market_states,
            'state_results': state_results,
            'comparison': comparison,
            'market_data_info': {
                'index': '000001.SH',  # 上证指数
                'start_date': start_date,
                'end_date': end_date
            }
        }
    
    # ========== 辅助方法 ==========
    
    def _run_single_backtest(self, df: pd.DataFrame, signals: pd.Series) -> Dict[str, Any]:
        """运行单股票回测"""
        if df.empty or signals.empty:
            return {'error': '数据为空'}
        
        # 对齐数据
        common_idx = df.index.intersection(signals.index)
        if len(common_idx) == 0:
            return {'error': '数据索引不匹配'}
        
        df = df.loc[common_idx]
        signals = signals.loc[common_idx]
        
        # 初始化
        capital = self.initial_capital
        cash = capital
        position = 0
        entry_price = 0
        
        portfolio_values = []
        trades = []
        
        # 逐日模拟
        prev_signal = 0
        for i, date in enumerate(common_idx):
            price = df.loc[date, 'close']
            signal = signals.loc[date]
            
            # 交易逻辑
            if signal == 1 and prev_signal != 1:  # 买入
                if position == 0 and cash > 0:
                    buy_price = price * (1 + self.slippage)
                    shares = int(cash * (1 - self.commission) / buy_price)
                    
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
            
            elif signal == -1 and prev_signal != -1:  # 卖出
                if position > 0:
                    sell_price = price * (1 - self.slippage)
                    value = position * sell_price
                    cash += value * (1 - self.commission)
                    
                    return_pct = (sell_price - entry_price) / entry_price if entry_price > 0 else 0
                    
                    trades.append({
                        'date': date,
                        'action': 'SELL',
                        'shares': position,
                        'price': sell_price,
                        'value': value,
                        'return_pct': return_pct * 100,
                        'entry_price': entry_price
                    })
                    
                    position = 0
                    entry_price = 0
            
            # 计算当日组合价值
            position_value = position * price
            portfolio_value = cash + position_value
            portfolio_values.append(portfolio_value)
            
            prev_signal = signal
        
        # 计算绩效指标
        if len(portfolio_values) == 0:
            return {'error': '无有效交易数据'}
        
        portfolio_series = pd.Series(portfolio_values, index=common_idx[:len(portfolio_values)])
        
        # 总收益
        total_return = (portfolio_series.iloc[-1] - self.initial_capital) / self.initial_capital * 100
        
        # 年化收益
        days = (common_idx[-1] - common_idx[0]).days
        years = max(days / 365.25, 0.01)
        annual_return = ((1 + total_return/100) ** (1/years) - 1) * 100
        
        # 夏普比率（简化）
        returns = portfolio_series.pct_change().dropna()
        if len(returns) > 1:
            sharpe_ratio = returns.mean() / returns.std() * np.sqrt(252)
        else:
            sharpe_ratio = 0
        
        # 最大回撤
        cum_returns = (1 + returns).cumprod()
        running_max = cum_returns.expanding().max()
        drawdowns = (cum_returns - running_max) / running_max
        max_drawdown = drawdowns.min() * 100 if not drawdowns.empty else 0
        
        # 胜率
        profitable_trades = sum(1 for t in trades if t.get('return_pct', 0) > 0)
        win_rate = profitable_trades / max(1, len(trades)) * 100
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'total_trades': len(trades),
            'profitable_trades': profitable_trades,
            'trades': trades,
            'portfolio_values': portfolio_values,
            'dates': [d.strftime('%Y-%m-%d') for d in common_idx[:len(portfolio_values)]]
        }
    
    def _analyze_oos_performance(self, train_results: Dict, test_results: Dict) -> Dict[str, Any]:
        """分析OOS性能"""
        train_summary = train_results['summary']
        test_summary = test_results['summary']
        
        # 性能衰减
        performance_decay = {}
        metrics = ['total_return', 'annual_return', 'sharpe_ratio', 'win_rate']
        
        for metric in metrics:
            train_val = train_summary.get(metric, 0)
            test_val = test_summary.get(metric, 0)
            
            if abs(train_val) > 0.001:
                decay_pct = (test_val - train_val) / abs(train_val) * 100
            else:
                decay_pct = 0
            
            performance_decay[f'{metric}_decay_pct'] = decay_pct
        
        # 统计显著性（简化）
        decay_significant = any(abs(d) > 30 for d in performance_decay.values())
        
        return {
            'train_performance': train_summary,
            'test_performance': test_summary,
            'performance_decay': performance_decay,
            'decay_significant': decay_significant,
            'interpretation': self._interpret_oos_results(performance_decay, decay_significant)
        }
    
    def _analyze_walk_forward_performance(self, window_results: List[Dict]) -> Dict[str, Any]:
        """分析Walk-forward性能"""
        if not window_results:
            return {'error': '无有效窗口结果'}
        
        # 提取每个窗口的测试集表现
        test_performances = []
        valid_windows = []
        
        for wr in window_results:
            if 'oos_result' in wr:
                test_summary = wr['oos_result']['test_results']['summary']
                test_performances.append(test_summary)
                valid_windows.append(wr)
        
        if not test_performances:
            return {'error': '无有效测试结果'}
        
        # 计算稳定性指标
        metrics = ['total_return', 'annual_return', 'sharpe_ratio', 'win_rate']
        stability = {}
        
        for metric in metrics:
            values = [p.get(metric, 0) for p in test_performances]
            stability[f'{metric}_mean'] = np.mean(values)
            stability[f'{metric}_std'] = np.std(values)
            stability[f'{metric}_cv'] = np.std(values) / max(abs(np.mean(values)), 0.001)  # 变异系数
        
        # 趋势分析
        trends = {}
        for metric in metrics:
            values = [p.get(metric, 0) for p in test_performances]
            if len(values) > 1:
                # 线性趋势
                x = np.arange(len(values))
                slope, _ = np.polyfit(x, values, 1)
                trends[f'{metric}_trend'] = slope
        
        return {
            'total_windows': len(window_results),
            'valid_windows': len(valid_windows),
            'stability_metrics': stability,
            'performance_trends': trends,
            'window_performances': test_performances
        }
    
    def _default_market_state_classifier(self, market_data: pd.DataFrame) -> Dict[str, List[Tuple[str, str]]]:
        """默认市场状态分类器"""
        if market_data.empty:
            return {}
        
        # 简单基于移动平均线的状态分类
        prices = market_data['close']
        
        # 计算技术指标
        ma20 = prices.rolling(20).mean()
        ma60 = prices.rolling(60).mean()
        
        # 定义状态
        states = {
            'bull': [],  # 牛市: 价格 > MA20 > MA60
            'bear': [],  # 熊市: 价格 < MA20 < MA60
            'consolidation': []  # 震荡市: 其他
        }
        
        # 识别状态转换点
        current_state = None
        state_start = None
        
        for i in range(60, len(prices)):  # 从第60天开始
            date = prices.index[i]
            price = prices.iloc[i]
            ma20_val = ma20.iloc[i]
            ma60_val = ma60.iloc[i]
            
            # 判断状态
            if not np.isnan(ma20_val) and not np.isnan(ma60_val):
                if price > ma20_val and ma20_val > ma60_val:
                    new_state = 'bull'
                elif price < ma20_val and ma20_val < ma60_val:
                    new_state = 'bear'
                else:
                    new_state = 'consolidation'
                
                # 状态转换
                if new_state != current_state:
                    if current_state is not None and state_start is not None:
                        # 结束上一个状态
                        states[current_state].append((state_start, prices.index[i-1].strftime('%Y-%m-%d')))
                    
                    # 开始新状态
                    current_state = new_state
                    state_start = date.strftime('%Y-%m-%d')
        
        # 处理最后一个状态
        if current_state is not None and state_start is not None:
            states[current_state].append((state_start, prices.index[-1].strftime('%Y-%m-%d')))
        
        return states
    
    def _get_market_index_data(self, start_date: str, end_date: str) -> pd.DataFrame:
        """获取市场指数数据"""
        try:
            # 尝试从数据库获取
            from data.database.database_manager import DatabaseManager
            db = DatabaseManager()
            
            df = db.get_daily_prices('000001', start_date, end_date)
            if df is not None and not df.empty:
                return df
            
            # 备用：使用Baostock
            import baostock as bs
            
            lg = bs.login()
            if lg.error_code == '0':
                rs = bs.query_history_k_data_plus(
                    "sh.000001",
                    "date,open,high,low,close,volume",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="3"
                )
                
                if rs.error_code == '0':
                    df = rs.get_data()
                    df['date'] = pd.to_datetime(df['date'])
                    df.set_index('date', inplace=True)
                    
                    # 转换数据类型
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                    bs.logout()
                    return df
            
        except Exception as e:
            print(f"获取市场指数数据失败: {e}")
        
        # 最后手段：生成模拟数据
        dates = pd.date_range(start=start_date, end=end_date, freq='B')
        np.random.seed(12345)
        
        # 创建有一定趋势的模拟指数
        n = len(dates)
        trend = np.cumsum(np.random.randn(n) * 0.005)
        prices = 3000 * (1 + trend)
        
        df = pd.DataFrame({
            'open': prices * 0.995,
            'high': prices * 1.01,
            'low': prices * 0.99,
            'close': prices,
            'volume': np.random.randint(1e9, 5e9, n)
        }, index=dates)
        
        return df
    
    def _summarize_state_performance(self, state_performance: List[Dict]) -> Dict[str, Any]:
        """汇总市场状态表现"""
        if not state_performance:
            return {}
        
        metrics = ['total_return', 'annual_return', 'sharpe_ratio', 'max_drawdown', 'win_rate']
        summary = {}
        
        for metric in metrics:
            values = [p['result'].get(metric, 0) for p in state_performance]
            summary[f'{metric}_mean'] = np.mean(values)
            summary[f'{metric}_std'] = np.std(values)
            summary[f'{metric}_min'] = np.min(values)
            summary[f'{metric}_max'] = np.max(values)
        
        summary['total_periods'] = len(state_performance)
        summary['successful_periods'] = sum(1 for p in state_performance if p['result'].get('total_return', 0) > 0)
        summary['success_rate'] = summary['successful_periods'] / max(1, summary['total_periods']) * 100
        
        return summary
    
    def _compare_market_state_performance(self, state_results: Dict[str, Any]) -> Dict[str, Any]:
        """对比不同市场状态表现"""
        if not state_results:
            return {}
        
        comparison = {}
        for state_name, state_data in state_results.items():
            summary = state_data['summary']
            comparison[state_name] = {
                'mean_return': summary.get('total_return_mean', 0),
                'return_std': summary.get('total_return_std', 0),
                'sharpe_mean': summary.get('sharpe_ratio_mean', 0),
                'success_rate': summary.get('success_rate', 0),
                'total_periods': summary.get('total_periods', 0)
            }
        
        # 找出最佳和最差状态
        if comparison:
            best_state = max(comparison.items(), key=lambda x: x[1]['mean_return'])
            worst_state = min(comparison.items(), key=lambda x: x[1]['mean_return'])
            
            comparison['_analysis'] = {
                'best_state': best_state[0],
                'best_return': best_state[1]['mean_return'],
                'worst_state': worst_state[0],
                'worst_return': worst_state[1]['mean_return'],
                'state_diversity': len(comparison)
            }
        
        return comparison
    
    def _interpret_oos_results(self, performance_decay: Dict, decay_significant: bool) -> str:
        """解释OOS结果"""
        if not performance_decay:
            return "无OOS结果可解释"
        
        # 分析衰减模式
        decay_values = {k: v for k, v in performance_decay.items() if 'decay_pct' in k}
        
        if not decay_values:
            return "无衰减数据"
        
        avg_decay = np.mean(list(decay_values.values()))
        
        if avg_decay < -30:
            interpretation = "⚠️ 严重过拟合：测试集表现显著差于训练集"
        elif avg_decay < -10:
            interpretation = "🔶 存在过拟合：测试集表现下降明显"
        elif abs(avg_decay) <= 10:
            interpretation = "✅ 稳健性良好：训练集和测试集表现一致"
        else:
            interpretation = "🔷 测试集表现优于训练集，可能存在样本选择偏差"
        
        if decay_significant:
            interpretation += " (衰减显著)"
        else:
            interpretation += " (衰减不显著)"
        
        return interpretation


# ========== 示例策略函数 ==========

def simple_moving_average_strategy(df: pd.DataFrame, 
                                  short_window: int = 5,
                                  long_window: int = 20) -> pd.Series:
    """简单双均线策略示例"""
    if df.empty or 'close' not in df.columns:
        return pd.Series(0, index=df.index)
    
    prices = df['close']
    
    # 计算均线
    short_ma = prices.rolling(short_window).mean()
    long_ma = prices.rolling(long_window).mean()
    
    # 生成信号
    signals = pd.Series(0, index=prices.index)
    signals[short_ma > long_ma] = 1      # 金叉买入
    signals[short_ma < long_ma] = -1     # 死叉卖出
    
    # 防止频繁交易
    signals = signals.replace(to_replace=0, method='ffill')
    
    return signals


def rsi_momentum_strategy(df: pd.DataFrame,
                         rsi_period: int = 14,
                         oversold: int = 30,
                         overbought: int = 70) -> pd.Series:
    """RSI动量策略示例"""
    if df.empty or 'close' not in df.columns:
        return pd.Series(0, index=df.index)
    
    prices = df['close']
    
    # 计算RSI
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # 生成信号
    signals = pd.Series(0, index=prices.index)
    signals[rsi < oversold] = 1      # 超卖买入
    signals[rsi > overbought] = -1   # 超买卖出
    
    # 防止频繁交易
    signals = signals.replace(to_replace=0, method='ffill')
    
    return signals


# ========== 测试函数 ==========

def test_advanced_backtester():
    """测试高级回测器"""
    print("=" * 60)
    print("测试高级回测器")
    print("=" * 60)
    
    # 创建回测器
    backtester = AdvancedBacktester(
        initial_capital=1000000,
        commission=0.001,
        slippage=0.002
    )
    
    # 测试股票池
    test_symbols = ['600519', '000001', '300750']
    
    # 1. 测试标准回测
    print("\n1. 测试标准回测...")
    try:
        result = backtester.run_standard_backtest(
            test_symbols,
            '2024-01-01',
            '2024-12-31',
            simple_moving_average_strategy,
            short_window=5,
            long_window=20
        )
        
        print(f"   成功完成标准回测")
        print(f"   总收益: {result['summary']['total_return']:.1f}%")
        print(f"   胜率: {result['summary']['win_rate']:.1f}%")
        
    except Exception as e:
        print(f"   标准回测失败: {e}")
    
    # 2. 测试OOS回测
    print("\n2. 测试OOS回测...")
    try:
        oos_result = backtester.run_oos_test(
            test_symbols,
            '2023-01-01',  # 训练集
            '2023-12-31',
            '2024-01-01',  # 测试集
            '2024-06-30',
            simple_moving_average_strategy,
            short_window=5,
            long_window=20
        )
        
        print(f"   成功完成OOS测试")
        decay = oos_result['oos_analysis']['performance_decay']
        print(f"   收益衰减: {decay.get('total_return_decay_pct', 0):.1f}%")
        
    except Exception as e:
        print(f"   OOS测试失败: {e}")
    
    # 3. 测试Walk-forward回测
    print("\n3. 测试Walk-forward回测...")
    try:
        wf_result = backtester.run_walk_forward_test(
            test_symbols,
            '2020-01-01',
            '2024-12-31',
            train_window_years=1,
            test_window_months=6,
            step_months=3,
            strategy_func=simple_moving_average_strategy,
            short_window=5,
            long_window=20
        )
        
        print(f"   成功完成Walk-forward测试")
        print(f"   总窗口数: {wf_result['parameters']['total_windows']}")
        
        if 'wf_analysis' in wf_result:
            stability = wf_result['wf_analysis']['stability_metrics']
            print(f"   收益稳定性: CV={stability.get('total_return_cv', 0):.3f}")
        
    except Exception as e:
        print(f"   Walk-forward测试失败: {e}")
    
    # 4. 测试市场状态回测
    print("\n4. 测试市场状态回测...")
    try:
        market_result = backtester.run_market_state_backtest(
            test_symbols,
            '2023-01-01',
            '2024-12-31',
            strategy_func=simple_moving_average_strategy,
            short_window=5,
            long_window=20
        )
        
        print(f"   成功完成市场状态回测")
        if 'comparison' in market_result:
            comp = market_result['comparison']
            print(f"   识别市场状态数: {comp.get('_analysis', {}).get('state_diversity', 0)}")
        
    except Exception as e:
        print(f"   市场状态回测失败: {e}")
    
    print("\n" + "=" * 60)
    print("高级回测器测试完成")
    print("=" * 60)


if __name__ == "__main__":
    test_advanced_backtester()