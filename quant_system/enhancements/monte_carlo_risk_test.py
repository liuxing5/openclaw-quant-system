#!/usr/bin/env python3
"""
蒙特卡洛风险测试 - 随机打乱时间序列检验策略稳定性
目的：检验策略是否高度依赖特定的市场时序（运气成分大）
如果打乱顺序后系统亏损惨重，说明策略抗风险能力弱
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Any, Optional, Callable
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')
import time
from tqdm import tqdm

# matplotlib是可选依赖，用于绘图
try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    plt = None

@dataclass
class MonteCarloResult:
    """蒙特卡洛模拟结果"""
    simulation_id: int
    shuffled_order: np.ndarray          # 打乱后的顺序
    original_returns: np.ndarray        # 原始收益率序列
    shuffled_returns: np.ndarray        # 打乱后的收益率序列
    portfolio_values: np.ndarray        # 组合价值序列
    total_return: float                 # 总收益率
    annual_return: float                # 年化收益率
    sharpe_ratio: float                 # 夏普比率
    max_drawdown: float                 # 最大回撤
    win_rate: float                     # 胜率
    
@dataclass
class RiskTestSummary:
    """风险测试摘要"""
    n_simulations: int                   # 模拟次数
    original_performance: Dict[str, float]  # 原始表现
    shuffled_performance_stats: Dict[str, Dict[str, float]]  # 打乱后表现统计
    timing_dependency_score: float       # 时序依赖分数 (0-1，越高越依赖时序)
    risk_assessment: str                 # 风险评估
    passing_rate: float                  # 通过率（盈利比例）
    confidence_level: float              # 置信水平
    
@dataclass
class ShufflingStrategy:
    """时间序列打乱策略"""
    name: str
    description: str
    shuffle_func: Callable[[np.ndarray, int], np.ndarray]  # 打乱函数
    
class MonteCarloRiskTester:
    """蒙特卡洛风险测试器"""
    
    def __init__(self, 
                 n_simulations: int = 1000,
                 confidence_level: float = 0.95,
                 random_seed: Optional[int] = 42):
        """
        初始化蒙特卡洛风险测试器
        
        Args:
            n_simulations: 蒙特卡洛模拟次数
            confidence_level: 置信水平
            random_seed: 随机种子
        """
        self.n_simulations = n_simulations
        self.confidence_level = confidence_level
        self.random_seed = random_seed
        
        if random_seed is not None:
            np.random.seed(random_seed)
        
        # 打乱策略
        self.shuffling_strategies = self._initialize_shuffling_strategies()
        
        # 结果存储
        self.results: List[MonteCarloResult] = []
        self.summary: Optional[RiskTestSummary] = None
        
    def _initialize_shuffling_strategies(self) -> List[ShufflingStrategy]:
        """初始化打乱策略"""
        strategies = []
        
        # 1. 完全随机打乱
        def completely_random_shuffle(returns: np.ndarray, seed: int) -> np.ndarray:
            np.random.seed(seed)
            shuffled = returns.copy()
            np.random.shuffle(shuffled)
            return shuffled
        
        strategies.append(ShufflingStrategy(
            name="completely_random",
            description="完全随机打乱 - 完全破坏时间序列结构",
            shuffle_func=completely_random_shuffle
        ))
        
        # 2. 区块随机打乱（保持局部相关性）
        def block_random_shuffle(returns: np.ndarray, seed: int, block_size: int = 5) -> np.ndarray:
            np.random.seed(seed)
            n = len(returns)
            shuffled = returns.copy()
            
            # 创建区块
            blocks = []
            for i in range(0, n, block_size):
                block = returns[i:i+block_size]
                if len(block) > 0:
                    blocks.append(block)
            
            # 随机打乱区块顺序
            np.random.shuffle(blocks)
            
            # 重新组合
            shuffled = np.concatenate(blocks)
            
            # 如果长度不匹配，调整
            if len(shuffled) > n:
                shuffled = shuffled[:n]
            elif len(shuffled) < n:
                # 重复部分数据
                repeat_times = (n + len(shuffled) - 1) // len(shuffled)
                shuffled = np.tile(shuffled, repeat_times)[:n]
            
            return shuffled
        
        strategies.append(ShufflingStrategy(
            name="block_random_5",
            description="区块随机打乱(5天) - 保持5天内局部相关性",
            shuffle_func=lambda returns, seed: block_random_shuffle(returns, seed, 5)
        ))
        
        # 3. 市场状态感知打乱（保持牛市/熊市状态）
        def market_state_shuffle(returns: np.ndarray, seed: int, state_window: int = 20) -> np.ndarray:
            np.random.seed(seed)
            n = len(returns)
            
            # 识别市场状态（简化版：基于滚动收益）
            rolling_return = pd.Series(returns).rolling(state_window).sum().fillna(0).values
            
            # 定义状态：上涨、下跌、震荡
            states = np.zeros(n, dtype=int)
            threshold = np.std(returns) * np.sqrt(state_window)
            
            for i in range(n):
                if rolling_return[i] > threshold:
                    states[i] = 1  # 上涨
                elif rolling_return[i] < -threshold:
                    states[i] = -1  # 下跌
                else:
                    states[i] = 0  # 震荡
            
            # 按状态分组打乱
            shuffled = returns.copy()
            
            for state in [-1, 0, 1]:
                state_indices = np.where(states == state)[0]
                if len(state_indices) > 1:
                    state_returns = returns[state_indices]
                    np.random.shuffle(state_returns)
                    shuffled[state_indices] = state_returns
            
            return shuffled
        
        strategies.append(ShufflingStrategy(
            name="market_state_aware",
            description="市场状态感知打乱 - 保持牛市/熊市状态内顺序",
            shuffle_func=market_state_shuffle
        ))
        
        # 4. 季节性打乱（保持月份/季度模式）
        def seasonal_shuffle(returns: np.ndarray, seed: int, dates: Optional[pd.DatetimeIndex] = None) -> np.ndarray:
            np.random.seed(seed)
            n = len(returns)
            shuffled = returns.copy()
            
            if dates is not None and len(dates) == n:
                # 提取月份
                months = dates.month.values
                
                # 按月分组打乱
                for month in range(1, 13):
                    month_indices = np.where(months == month)[0]
                    if len(month_indices) > 1:
                        month_returns = returns[month_indices]
                        np.random.shuffle(month_returns)
                        shuffled[month_indices] = month_returns
            else:
                # 如果没有日期，使用简单分组
                group_size = 21  # 约1个月
                for i in range(0, n, group_size):
                    end_idx = min(i + group_size, n)
                    group = returns[i:end_idx]
                    if len(group) > 1:
                        np.random.shuffle(group)
                        shuffled[i:end_idx] = group
            
            return shuffled
        
        strategies.append(ShufflingStrategy(
            name="seasonal",
            description="季节性打乱 - 保持月份内顺序",
            shuffle_func=seasonal_shuffle
        ))
        
        return strategies
    
    def calculate_portfolio_performance(self, returns: np.ndarray) -> Dict[str, float]:
        """计算组合表现指标"""
        n = len(returns)
        
        if n == 0:
            return {
                'total_return': 0.0,
                'annual_return': 0.0,
                'sharpe_ratio': 0.0,
                'max_drawdown': 0.0,
                'win_rate': 0.0,
                'profit_factor': 0.0
            }
        
        # 累计收益
        cumulative = (1 + returns).cumprod()
        total_return = cumulative[-1] - 1 if len(cumulative) > 0 else 0.0
        
        # 年化收益
        years = n / 252.0  # 交易日转年
        annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0
        
        # 夏普比率（无风险利率3%）
        excess_returns = returns - 0.03/252
        sharpe_ratio = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252) if np.std(excess_returns) > 0 else 0.0
        
        # 最大回撤
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = (cumulative - running_max) / running_max
        max_drawdown = np.min(drawdowns) if len(drawdowns) > 0 else 0.0
        
        # 胜率
        win_rate = np.mean(returns > 0)
        
        # 盈亏比
        positive_returns = returns[returns > 0]
        negative_returns = returns[returns < 0]
        
        if len(negative_returns) > 0 and np.sum(np.abs(negative_returns)) > 0:
            profit_factor = np.sum(positive_returns) / np.sum(np.abs(negative_returns))
        else:
            profit_factor = float('inf') if len(positive_returns) > 0 else 0.0
        
        return {
            'total_return': float(total_return),
            'annual_return': float(annual_return),
            'sharpe_ratio': float(sharpe_ratio),
            'max_drawdown': float(max_drawdown),
            'win_rate': float(win_rate),
            'profit_factor': float(profit_factor)
        }
    
    def run_single_simulation(self, 
                            simulation_id: int,
                            returns: np.ndarray,
                            shuffle_strategy: ShufflingStrategy,
                            dates: Optional[pd.DatetimeIndex] = None) -> MonteCarloResult:
        """运行单次模拟"""
        
        # 打乱收益率序列
        if shuffle_strategy.name == "seasonal" and dates is not None:
            shuffled_returns = shuffle_strategy.shuffle_func(returns, simulation_id, dates)
        else:
            shuffled_returns = shuffle_strategy.shuffle_func(returns, simulation_id)
        
        # 计算打乱后的表现
        performance = self.calculate_portfolio_performance(shuffled_returns)
        
        # 创建结果
        result = MonteCarloResult(
            simulation_id=simulation_id,
            shuffled_order=np.argsort(np.random.randn(len(returns))),  # 简化顺序
            original_returns=returns,
            shuffled_returns=shuffled_returns,
            portfolio_values=(1 + shuffled_returns).cumprod(),
            total_return=performance['total_return'],
            annual_return=performance['annual_return'],
            sharpe_ratio=performance['sharpe_ratio'],
            max_drawdown=performance['max_drawdown'],
            win_rate=performance['win_rate']
        )
        
        return result
    
    def run_monte_carlo_test(self,
                            returns: pd.Series,
                            shuffle_strategy_name: str = "completely_random",
                            dates: Optional[pd.DatetimeIndex] = None) -> RiskTestSummary:
        """
        运行蒙特卡洛风险测试
        
        Args:
            returns: 收益率序列
            shuffle_strategy_name: 打乱策略名称
            dates: 日期索引（用于季节性打乱）
            
        Returns:
            风险测试摘要
        """
        start_time = time.time()
        
        # 找到对应的打乱策略
        shuffle_strategy = None
        for strategy in self.shuffling_strategies:
            if strategy.name == shuffle_strategy_name:
                shuffle_strategy = strategy
                break
        
        if shuffle_strategy is None:
            raise ValueError(f"未知的打乱策略: {shuffle_strategy_name}")
        
        # 转换为numpy数组
        returns_array = returns.values if isinstance(returns, pd.Series) else returns
        
        print(f"开始蒙特卡洛风险测试")
        print(f"  数据长度: {len(returns_array)} 天 (~{len(returns_array)/252:.1f} 年)")
        print(f"  模拟次数: {self.n_simulations}")
        print(f"  打乱策略: {shuffle_strategy.description}")
        
        # 计算原始表现
        original_performance = self.calculate_portfolio_performance(returns_array)
        
        # 运行蒙特卡洛模拟
        self.results = []
        
        for i in tqdm(range(self.n_simulations), desc="蒙特卡洛模拟"):
            try:
                result = self.run_single_simulation(
                    simulation_id=i,
                    returns=returns_array,
                    shuffle_strategy=shuffle_strategy,
                    dates=dates
                )
                self.results.append(result)
            except Exception as e:
                print(f"模拟 {i} 失败: {e}")
        
        # 收集打乱后的表现数据
        shuffled_performances = {
            'total_return': [r.total_return for r in self.results],
            'annual_return': [r.annual_return for r in self.results],
            'sharpe_ratio': [r.sharpe_ratio for r in self.results],
            'max_drawdown': [r.max_drawdown for r in self.results],
            'win_rate': [r.win_rate for r in self.results]
        }
        
        # 计算统计量
        performance_stats = {}
        for metric, values in shuffled_performances.items():
            if values:
                performance_stats[metric] = {
                    'mean': np.mean(values),
                    'std': np.std(values),
                    'min': np.min(values),
                    'max': np.max(values),
                    'median': np.median(values),
                    'p5': np.percentile(values, 5),
                    'p95': np.percentile(values, 95)
                }
        
        # 计算时序依赖分数
        # 比较原始表现与打乱后表现的差异
        timing_dependency_score = self._calculate_timing_dependency(
            original_performance, shuffled_performances
        )
        
        # 计算通过率（盈利比例）
        profitable_simulations = sum(1 for r in self.results if r.total_return > 0)
        passing_rate = profitable_simulations / len(self.results) if self.results else 0.0
        
        # 风险评估
        risk_assessment = self._assess_risk(timing_dependency_score, passing_rate)
        
        # 创建摘要
        self.summary = RiskTestSummary(
            n_simulations=len(self.results),
            original_performance=original_performance,
            shuffled_performance_stats=performance_stats,
            timing_dependency_score=timing_dependency_score,
            risk_assessment=risk_assessment,
            passing_rate=passing_rate,
            confidence_level=self.confidence_level
        )
        
        elapsed_time = time.time() - start_time
        
        print(f"\n测试完成，耗时: {elapsed_time:.2f}秒")
        print(f"时序依赖分数: {timing_dependency_score:.3f}")
        print(f"通过率（盈利比例）: {passing_rate*100:.1f}%")
        print(f"风险评估: {risk_assessment}")
        
        return self.summary
    
    def _calculate_timing_dependency(self, 
                                   original_performance: Dict[str, float],
                                   shuffled_performances: Dict[str, List[float]]) -> float:
        """计算时序依赖分数"""
        
        # 关键指标：总收益率、夏普比率
        key_metrics = ['total_return', 'sharpe_ratio']
        dependency_scores = []
        
        for metric in key_metrics:
            if metric in original_performance and metric in shuffled_performances:
                original_value = original_performance[metric]
                shuffled_values = shuffled_performances[metric]
                
                if shuffled_values:
                    # 计算原始值在打乱后分布中的分位数
                    sorted_values = np.sort(shuffled_values)
                    rank = np.searchsorted(sorted_values, original_value)
                    quantile = rank / len(sorted_values)
                    
                    # 计算依赖分数：距离中位数的距离（绝对值）
                    dependency_score = abs(quantile - 0.5) * 2  # 归一化到0-1
                    dependency_scores.append(dependency_score)
        
        # 返回平均依赖分数
        return np.mean(dependency_scores) if dependency_scores else 0.0
    
    def _assess_risk(self, timing_dependency_score: float, passing_rate: float) -> str:
        """风险评估"""
        
        if passing_rate < 0.3:
            return "高风险 - 策略在随机时序下多数亏损"
        elif passing_rate < 0.5:
            if timing_dependency_score > 0.7:
                return "高风险 - 高度依赖特定时序且盈利不稳定"
            else:
                return "中高风险 - 盈利不稳定"
        elif passing_rate < 0.7:
            if timing_dependency_score > 0.7:
                return "中风险 - 高度依赖时序但有一定盈利性"
            else:
                return "中风险 - 中等盈利性和时序依赖性"
        else:  # passing_rate >= 0.7
            if timing_dependency_score > 0.7:
                return "低风险 - 虽依赖时序但盈利性强"
            else:
                return "低风险 - 稳健策略，不依赖特定时序"
    
    def generate_risk_report(self) -> Dict[str, Any]:
        """生成风险报告"""
        if self.summary is None:
            raise ValueError("请先运行蒙特卡洛测试")
        
        report = {
            'test_configuration': {
                'n_simulations': self.n_simulations,
                'confidence_level': self.confidence_level
            },
            'original_performance': self.summary.original_performance,
            'monte_carlo_results': {
                'timing_dependency_score': self.summary.timing_dependency_score,
                'passing_rate': self.summary.passing_rate,
                'risk_assessment': self.summary.risk_assessment,
                'performance_distribution': self.summary.shuffled_performance_stats
            },
            'interpretation': self._generate_interpretation(),
            'recommendations': self._generate_recommendations()
        }
        
        return report
    
    def _generate_interpretation(self) -> Dict[str, str]:
        """生成结果解释"""
        interpretation = {}
        
        # 时序依赖解释
        timing_score = self.summary.timing_dependency_score
        if timing_score < 0.3:
            interpretation['timing_dependency'] = "策略不依赖特定市场时序，具有较强的普适性"
        elif timing_score < 0.6:
            interpretation['timing_dependency'] = "策略对市场时序有一定依赖性，但仍有较好的适应性"
        else:
            interpretation['timing_dependency'] = "策略高度依赖特定市场时序，可能在特定市场环境下表现良好但缺乏普适性"
        
        # 通过率解释
        passing_rate = self.summary.passing_rate
        if passing_rate < 0.3:
            interpretation['passing_rate'] = "策略在随机时序下多数亏损，可能过度拟合历史数据"
        elif passing_rate < 0.5:
            interpretation['passing_rate'] = "策略盈利性不稳定，需谨慎使用"
        elif passing_rate < 0.7:
            interpretation['passing_rate'] = "策略有一定盈利性，但仍需风险管理"
        else:
            interpretation['passing_rate'] = "策略在随机时序下表现稳健，具有较强的抗风险能力"
        
        # 综合风险评估解释
        risk_assessment = self.summary.risk_assessment
        if "高风险" in risk_assessment:
            interpretation['overall_risk'] = "策略风险较高，建议重新设计或严格风控"
        elif "中风险" in risk_assessment:
            interpretation['overall_risk'] = "策略风险中等，可使用但需配合适当风控"
        else:
            interpretation['overall_risk'] = "策略风险较低，可考虑实际应用"
        
        return interpretation
    
    def _generate_recommendations(self) -> List[Dict[str, str]]:
        """生成建议"""
        recommendations = []
        
        timing_score = self.summary.timing_dependency_score
        passing_rate = self.summary.passing_rate
        
        # 基于时序依赖性的建议
        if timing_score > 0.7:
            recommendations.append({
                'category': 'timing_dependency',
                'priority': '高',
                'suggestion': '策略高度依赖市场时序，建议：1) 缩短调仓周期 2) 增加市场状态判断 3) 使用动态参数调整'
            })
        elif timing_score > 0.5:
            recommendations.append({
                'category': 'timing_dependency',
                'priority': '中',
                'suggestion': '策略对时序有一定依赖，建议：1) 增加样本外测试 2) 使用滚动窗口优化 3) 监控市场环境变化'
            })
        
        # 基于通过率的建议
        if passing_rate < 0.3:
            recommendations.append({
                'category': 'profitability',
                'priority': '高',
                'suggestion': '策略在随机时序下多数亏损，可能过度拟合，建议：1) 简化策略逻辑 2) 减少参数数量 3) 重新评估因子有效性'
            })
        elif passing_rate < 0.5:
            recommendations.append({
                'category': 'profitability',
                'priority': '中',
                'suggestion': '策略盈利性不稳定，建议：1) 增加止损机制 2) 控制仓位规模 3) 定期重新评估策略'
            })
        
        # 通用建议
        recommendations.append({
            'category': 'general',
            'priority': '低',
            'suggestion': '定期运行蒙特卡洛测试监控策略稳定性，建议每季度至少测试一次'
        })
        
        return recommendations
    
    def plot_results(self, save_path: Optional[str] = None):
        """绘制结果图表"""
        if not HAS_MATPLOTLIB:
            print("警告: matplotlib未安装，跳过绘图功能")
            return
            
        if not self.results:
            print("无结果可绘制")
            return
        
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        
        # 1. 总收益率分布
        ax1 = axes[0, 0]
        total_returns = [r.total_return for r in self.results]
        original_return = self.summary.original_performance['total_return']
        
        ax1.hist(total_returns, bins=30, alpha=0.7, edgecolor='black')
        ax1.axvline(x=original_return, color='red', linestyle='--', linewidth=2, label=f'原始收益: {original_return:.2%}')
        ax1.axvline(x=0, color='gray', linestyle='-', linewidth=1, alpha=0.5)
        ax1.set_xlabel('总收益率')
        ax1.set_ylabel('频数')
        ax1.set_title('总收益率分布（蒙特卡洛模拟）')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # 2. 夏普比率分布
        ax2 = axes[0, 1]
        sharpe_ratios = [r.sharpe_ratio for r in self.results]
        original_sharpe = self.summary.original_performance['sharpe_ratio']
        
        ax2.hist(sharpe_ratios, bins=30, alpha=0.7, edgecolor='black', color='green')
        ax2.axvline(x=original_sharpe, color='red', linestyle='--', linewidth=2, label=f'原始夏普: {original_sharpe:.2f}')
        ax2.axvline(x=0, color='gray', linestyle='-', linewidth=1, alpha=0.5)
        ax2.set_xlabel('夏普比率')
        ax2.set_ylabel('频数')
        ax2.set_title('夏普比率分布')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        # 3. 最大回撤分布
        ax3 = axes[1, 0]
        max_drawdowns = [r.max_drawdown for r in self.results]
        original_drawdown = self.summary.original_performance['max_drawdown']
        
        ax3.hist(max_drawdowns, bins=30, alpha=0.7, edgecolor='black', color='orange')
        ax3.axvline(x=original_drawdown, color='red', linestyle='--', linewidth=2, label=f'原始回撤: {original_drawdown:.2%}')
        ax3.set_xlabel('最大回撤')
        ax3.set_ylabel('频数')
        ax3.set_title('最大回撤分布')
        ax3.legend()
        ax3.grid(True, alpha=0.3)
        
        # 4. 通过率饼图
        ax4 = axes[1, 1]
        profitable = sum(1 for r in self.results if r.total_return > 0)
        unprofitable = len(self.results) - profitable
        
        labels = ['盈利', '亏损']
        sizes = [profitable, unprofitable]
        colors = ['#66b3ff', '#ff9999']
        
        ax4.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=90)
        ax4.set_title(f'通过率: {profitable/len(self.results)*100:.1f}%')
        
        plt.suptitle(f'蒙特卡洛风险测试结果 (n={len(self.results)})', fontsize=16)
        plt.tight_layout()
        
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"图表已保存至: {save_path}")
        
        plt.show()

# ========== 示例使用 ==========

def example_usage():
    """示例使用方法"""
    print("蒙特卡洛风险测试示例")
    print("=" * 60)
    
    # 生成测试数据（3年收益率序列）
    np.random.seed(42)
    n_days = 756  # 3年交易日 ≈ 756天
    
    # 创建有趋势的收益率序列（模拟一个"好"策略）
    base_date = pd.Timestamp('2020-01-01')
    dates = pd.date_range(start=base_date, periods=n_days, freq='B')
    
    # 模拟策略收益率（正期望但有时序依赖性）
    trend_component = np.cumsum(np.random.randn(n_days) * 0.001)
    seasonal_component = np.sin(np.arange(n_days) * 2 * np.pi / 63) * 0.002  # 季度效应
    noise = np.random.randn(n_days) * 0.015
    
    returns = 0.0003 + trend_component * 0.1 + seasonal_component + noise  # 日均收益约0.03%
    returns_series = pd.Series(returns, index=dates)
    
    print(f"生成 {n_days} 天收益率数据")
    print(f"平均日收益: {returns.mean()*100:.4f}%")
    print(f"年化收益: {(1 + returns.mean())**252 - 1:.2%}")
    print(f"年化波动: {returns.std() * np.sqrt(252):.2%}")
    
    # 创建测试器
    tester = MonteCarloRiskTester(
        n_simulations=500,  # 减少次数以加快测试
        confidence_level=0.95,
        random_seed=42
    )
    
    # 运行测试（完全随机打乱）
    print("\n运行蒙特卡洛风险测试...")
    summary = tester.run_monte_carlo_test(
        returns=returns_series,
        shuffle_strategy_name="completely_random",
        dates=dates
    )
    
    print("\n原始表现:")
    for metric, value in summary.original_performance.items():
        print(f"  {metric}: {value:.4f}")
    
    print("\n蒙特卡洛统计（打乱后）:")
    for metric, stats in summary.shuffled_performance_stats.items():
        print(f"  {metric}: 均值={stats['mean']:.4f}, 标准差={stats['std']:.4f}, "
              f"范围[{stats['min']:.4f}, {stats['max']:.4f}]")
    
    print(f"\n时序依赖分数: {summary.timing_dependency_score:.3f}")
    print(f"通过率: {summary.passing_rate*100:.1f}%")
    print(f"风险评估: {summary.risk_assessment}")
    
    # 生成报告
    report = tester.generate_risk_report()
    
    print("\n报告解读:")
    for category, text in report['interpretation'].items():
        print(f"  {category}: {text}")
    
    print("\n建议:")
    for rec in report['recommendations']:
        print(f"  [{rec['priority']}] {rec['suggestion']}")
    
    # 绘制结果
    print("\n绘制结果图表...")
    tester.plot_results()
    
    # 测试不同打乱策略
    print("\n测试不同打乱策略...")
    strategies_to_test = ["completely_random", "block_random_5", "market_state_aware"]
    
    for strategy_name in strategies_to_test:
        print(f"\n策略: {strategy_name}")
        try:
            tester2 = MonteCarloRiskTester(n_simulations=100, random_seed=42)
            summary2 = tester2.run_monte_carlo_test(
                returns=returns_series,
                shuffle_strategy_name=strategy_name,
                dates=dates
            )
            print(f"  时序依赖分数: {summary2.timing_dependency_score:.3f}")
            print(f"  通过率: {summary2.passing_rate*100:.1f}%")
        except Exception as e:
            print(f"  测试失败: {e}")

if __name__ == "__main__":
    example_usage()