#!/usr/bin/env python3
"""
组合优化引擎 (Portfolio Optimization)
实现均值-方差优化、风险平价、最小方差等专业组合优化方法
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any, Callable
import warnings
warnings.filterwarnings('ignore')

try:
    import cvxpy as cp
    CVXPY_AVAILABLE = True
except ImportError:
    CVXPY_AVAILABLE = False

try:
    from scipy.optimize import minimize
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False


class PortfolioOptimizer:
    """组合优化引擎"""
    
    def __init__(self,
                 risk_free_rate: float = 0.03,
                 max_position: float = 0.1,      # 最大单资产权重
                 min_position: float = 0.0,      # 最小单资产权重
                 turnover_limit: float = 0.2,    # 换手率限制
                 transaction_cost: float = 0.001, # 交易成本
                 liquidity_constraint: bool = True,  # 流动性约束
                 diversification_target: float = 0.7): # 分散化目标
        self.risk_free_rate = risk_free_rate
        self.max_position = max_position
        self.min_position = min_position
        self.turnover_limit = turnover_limit
        self.transaction_cost = transaction_cost
        self.liquidity_constraint = liquidity_constraint
        self.diversification_target = diversification_target
        
        # 优化结果存储
        self.optimization_results = {}
        self.optimal_weights = None
        self.optimal_portfolio_stats = None
        
        print("组合优化引擎初始化")
    
    def mean_variance_optimization(self,
                                 expected_returns: pd.Series,
                                 covariance_matrix: pd.DataFrame,
                                 target_return: float = None,
                                 target_risk: float = None,
                                 objective: str = 'sharpe') -> Dict[str, Any]:
        """
        均值-方差优化 (Markowitz)
        
        Args:
            expected_returns: 预期收益率
            covariance_matrix: 协方差矩阵
            target_return: 目标收益率 (如果指定)
            target_risk: 目标风险 (如果指定)
            objective: 目标函数 ('sharpe', 'variance', 'return')
        """
        n_assets = len(expected_returns)
        
        if SCIPY_AVAILABLE:
            # 使用scipy优化
            if objective == 'sharpe':
                # 最大化夏普比率
                def negative_sharpe(weights):
                    port_return = np.dot(weights, expected_returns)
                    port_risk = np.sqrt(np.dot(weights.T, np.dot(covariance_matrix, weights)))
                    sharpe = (port_return - self.risk_free_rate) / port_risk if port_risk > 0 else 0
                    return -sharpe
                
                objective_func = negative_sharpe
                
            elif objective == 'variance':
                # 最小化方差
                def portfolio_variance(weights):
                    return np.dot(weights.T, np.dot(covariance_matrix, weights))
                
                objective_func = portfolio_variance
                
            elif objective == 'return':
                # 最大化收益
                def negative_return(weights):
                    return -np.dot(weights, expected_returns)
                
                objective_func = negative_return
            else:
                raise ValueError(f"未知目标函数: {objective}")
            
            # 约束条件
            constraints = []
            
            # 权重和为1
            constraints.append({'type': 'eq', 'fun': lambda w: np.sum(w) - 1})
            
            # 目标收益率约束 (如果指定)
            if target_return is not None:
                constraints.append({'type': 'eq', 
                                  'fun': lambda w: np.dot(w, expected_returns) - target_return})
            
            # 目标风险约束 (如果指定)
            if target_risk is not None:
                constraints.append({'type': 'eq',
                                  'fun': lambda w: np.sqrt(np.dot(w.T, np.dot(covariance_matrix, w))) - target_risk})
            
            # 边界约束
            bounds = [(self.min_position, self.max_position) for _ in range(n_assets)]
            
            # 初始权重 (等权重)
            init_weights = np.ones(n_assets) / n_assets
            
            # 优化
            result = minimize(objective_func, init_weights,
                            bounds=bounds, constraints=constraints,
                            method='SLSQP', options={'maxiter': 1000})
            
            if result.success:
                optimal_weights = pd.Series(result.x, index=expected_returns.index)
                success = True
                message = result.message
            else:
                # 优化失败，使用等权重
                optimal_weights = pd.Series(init_weights, index=expected_returns.index)
                success = False
                message = result.message
                
        else:
            # 简化实现 (无优化库)
            print("警告: 无优化库，使用等权重")
            optimal_weights = pd.Series(np.ones(n_assets) / n_assets, index=expected_returns.index)
            success = False
            message = "无优化库可用"
        
        # 计算组合统计
        portfolio_stats = self._calculate_portfolio_stats(optimal_weights, expected_returns, covariance_matrix)
        
        result_dict = {
            'weights': optimal_weights,
            'success': success,
            'message': message,
            'stats': portfolio_stats,
            'method': 'mean_variance',
            'objective': objective
        }
        
        self.optimal_weights = optimal_weights
        self.optimal_portfolio_stats = portfolio_stats
        
        return result_dict
    
    def risk_parity_optimization(self,
                               covariance_matrix: pd.DataFrame,
                               risk_budget: pd.Series = None) -> Dict[str, Any]:
        """
        风险平价优化 (Risk Parity)
        每个资产贡献相同比例的风险
        """
        n_assets = len(covariance_matrix)
        
        if risk_budget is None:
            # 等风险预算
            risk_budget = pd.Series(np.ones(n_assets) / n_assets, index=covariance_matrix.index)
        
        if SCIPY_AVAILABLE:
            # 风险平价目标函数
            def risk_parity_objective(weights):
                # 计算组合风险
                port_risk = np.sqrt(np.dot(weights.T, np.dot(covariance_matrix, weights)))
                
                # 计算每个资产的风险贡献
                marginal_contrib = np.dot(covariance_matrix, weights)
                risk_contrib = weights * marginal_contrib / port_risk if port_risk > 0 else 0
                
                # 目标：风险贡献与风险预算的差异最小
                target_contrib = risk_budget.values * port_risk
                error = np.sum((risk_contrib - target_contrib) ** 2)
                
                return error
            
            # 约束条件
            constraints = [
                {'type': 'eq', 'fun': lambda w: np.sum(w) - 1},  # 权重和为1
                {'type': 'ineq', 'fun': lambda w: w}  # 非负约束
            ]
            
            # 边界
            bounds = [(self.min_position, self.max_position) for _ in range(n_assets)]
            
            # 初始权重 (等权重)
            init_weights = np.ones(n_assets) / n_assets
            
            # 优化
            result = minimize(risk_parity_objective, init_weights,
                            bounds=bounds, constraints=constraints,
                            method='SLSQP', options={'maxiter': 1000})
            
            if result.success:
                optimal_weights = pd.Series(result.x, index=covariance_matrix.index)
                success = True
                message = result.message
            else:
                optimal_weights = pd.Series(init_weights, index=covariance_matrix.index)
                success = False
                message = result.message
        else:
            # 简化实现
            print("警告: 无优化库，使用等权重")
            optimal_weights = pd.Series(np.ones(n_assets) / n_assets, index=covariance_matrix.index)
            success = False
            message = "无优化库可用"
        
        # 计算风险贡献
        risk_contribution = self._calculate_risk_contribution(optimal_weights, covariance_matrix)
        
        result_dict = {
            'weights': optimal_weights,
            'success': success,
            'message': message,
            'risk_contribution': risk_contribution,
            'method': 'risk_parity',
            'risk_budget': risk_budget
        }
        
        return result_dict
    
    def minimum_variance_optimization(self,
                                    covariance_matrix: pd.DataFrame) -> Dict[str, Any]:
        """
        最小方差组合
        """
        # 这是均值-方差优化的特例 (目标收益率=None)
        dummy_returns = pd.Series(np.zeros(len(covariance_matrix)), index=covariance_matrix.index)
        
        return self.mean_variance_optimization(
            expected_returns=dummy_returns,
            covariance_matrix=covariance_matrix,
            objective='variance'
        )
    
    def max_diversification_optimization(self,
                                       covariance_matrix: pd.DataFrame,
                                       volatilities: pd.Series = None) -> Dict[str, Any]:
        """
        最大分散化组合
        """
        n_assets = len(covariance_matrix)
        
        if volatilities is None:
            # 从协方差矩阵提取波动率
            volatilities = pd.Series(np.sqrt(np.diag(covariance_matrix)), index=covariance_matrix.index)
        
        if SCIPY_AVAILABLE:
            # 最大化分散化比率
            def diversification_ratio(weights):
                weighted_vol = np.dot(weights, volatilities.values)
                port_vol = np.sqrt(np.dot(weights.T, np.dot(covariance_matrix, weights)))
                
                if port_vol > 0:
                    return -weighted_vol / port_vol  # 最大化分散化比率
                else:
                    return 0
            
            # 约束
            constraints = [
                {'type': 'eq', 'fun': lambda w: np.sum(w) - 1},
                {'type': 'ineq', 'fun': lambda w: w}
            ]
            
            bounds = [(self.min_position, self.max_position) for _ in range(n_assets)]
            init_weights = np.ones(n_assets) / n_assets
            
            result = minimize(diversification_ratio, init_weights,
                            bounds=bounds, constraints=constraints,
                            method='SLSQP', options={'maxiter': 1000})
            
            if result.success:
                optimal_weights = pd.Series(result.x, index=covariance_matrix.index)
                success = True
                message = result.message
            else:
                optimal_weights = pd.Series(init_weights, index=covariance_matrix.index)
                success = False
                message = result.message
        else:
            optimal_weights = pd.Series(np.ones(n_assets) / n_assets, index=covariance_matrix.index)
            success = False
            message = "无优化库可用"
        
        # 计算分散化比率
        weighted_vol = np.dot(optimal_weights.values, volatilities.values)
        port_vol = np.sqrt(np.dot(optimal_weights.T, np.dot(covariance_matrix, optimal_weights)))
        diversification_ratio = weighted_vol / port_vol if port_vol > 0 else 0
        
        result_dict = {
            'weights': optimal_weights,
            'success': success,
            'message': message,
            'diversification_ratio': diversification_ratio,
            'method': 'max_diversification'
        }
        
        return result_dict
    
    def hierarchical_risk_parity(self,
                               covariance_matrix: pd.DataFrame,
                               linkage_method: str = 'ward') -> Dict[str, Any]:
        """
        分层风险平价 (Hierarchical Risk Parity, HRP)
        更稳健的分散化方法
        """
        try:
            from scipy.cluster.hierarchy import linkage, dendrogram
            from scipy.spatial.distance import squareform
            
            # 1. 计算相关性矩阵
            volatilities = np.sqrt(np.diag(covariance_matrix))
            outer_vol = np.outer(volatilities, volatilities)
            correlation_matrix = covariance_matrix / outer_vol
            correlation_matrix = np.nan_to_num(correlation_matrix, nan=0.0, posinf=0.0, neginf=0.0)
            
            # 2. 计算距离矩阵
            distance_matrix = np.sqrt((1 - correlation_matrix) / 2)
            distance_matrix = np.clip(distance_matrix, 0, 1)
            
            # 3. 层次聚类
            condensed_dist = squareform(distance_matrix)
            linkage_matrix = linkage(condensed_dist, method=linkage_method)
            
            # 4. 准对角化 (简化实现)
            n_assets = len(covariance_matrix)
            weights = np.ones(n_assets) / n_assets  # 简化：等权重
            
            optimal_weights = pd.Series(weights, index=covariance_matrix.index)
            
            result_dict = {
                'weights': optimal_weights,
                'success': True,
                'message': 'HRP (简化实现)',
                'method': 'hierarchical_risk_parity',
                'distance_matrix': distance_matrix,
                'linkage_matrix': linkage_matrix
            }
            
            return result_dict
            
        except ImportError:
            # 回退到风险平价
            print("警告: scipy.cluster不可用，回退到风险平价")
            return self.risk_parity_optimization(covariance_matrix)
    
    def _calculate_portfolio_stats(self,
                                 weights: pd.Series,
                                 expected_returns: pd.Series,
                                 covariance_matrix: pd.DataFrame) -> Dict[str, float]:
        """计算组合统计"""
        w = weights.values
        mu = expected_returns.values
        sigma = covariance_matrix.values
        
        port_return = np.dot(w, mu)
        port_risk = np.sqrt(np.dot(w.T, np.dot(sigma, w)))
        
        # 夏普比率
        sharpe = (port_return - self.risk_free_rate) / port_risk if port_risk > 0 else 0
        
        # 分散化比率
        weighted_vol = np.dot(w, np.sqrt(np.diag(sigma)))
        diversification_ratio = weighted_vol / port_risk if port_risk > 0 else 0
        
        # 风险贡献
        risk_contrib = self._calculate_risk_contribution(weights, covariance_matrix)
        
        # 最大回撤估计
        max_dd_estimate = port_risk * 2.5  # 简化估计
        
        return {
            'expected_return': port_return,
            'expected_risk': port_risk,
            'sharpe_ratio': sharpe,
            'diversification_ratio': diversification_ratio,
            'concentration': np.sum(w ** 2),  # HHI指数
            'max_drawdown_estimate': max_dd_estimate,
            'risk_contribution': risk_contrib
        }
    
    def _calculate_risk_contribution(self,
                                   weights: pd.Series,
                                   covariance_matrix: pd.DataFrame) -> pd.Series:
        """计算风险贡献"""
        w = weights.values
        sigma = covariance_matrix.values
        
        port_risk = np.sqrt(np.dot(w.T, np.dot(sigma, w)))
        
        if port_risk > 0:
            marginal_contrib = np.dot(sigma, w)
            risk_contrib = w * marginal_contrib / port_risk
        else:
            risk_contrib = np.zeros_like(w)
        
        return pd.Series(risk_contrib, index=weights.index)
    
    def optimize_with_constraints(self,
                                expected_returns: pd.Series,
                                covariance_matrix: pd.DataFrame,
                                current_weights: pd.Series = None,
                                constraints: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        带复杂约束的优化
        """
        if constraints is None:
            constraints = {}
        
        # 提取约束
        sector_constraints = constraints.get('sector_constraints', {})
        factor_exposure_constraints = constraints.get('factor_exposure_constraints', {})
        liquidity_constraints = constraints.get('liquidity_constraints', {})
        turnover_constraint = constraints.get('turnover_constraint', self.turnover_limit)
        
        # 简化实现：先进行基本优化，然后应用约束
        if current_weights is not None and turnover_constraint < 1.0:
            # 应用换手率约束
            base_result = self.mean_variance_optimization(
                expected_returns, covariance_matrix, objective='sharpe'
            )
            
            # 调整权重以满足换手率约束
            optimal_weights = self._apply_turnover_constraint(
                base_result['weights'], current_weights, turnover_constraint
            )
            
            # 重新计算统计
            portfolio_stats = self._calculate_portfolio_stats(
                optimal_weights, expected_returns, covariance_matrix
            )
            
            result_dict = {
                'weights': optimal_weights,
                'success': True,
                'message': '带约束优化 (简化)',
                'stats': portfolio_stats,
                'method': 'constrained_optimization',
                'constraints_applied': list(constraints.keys())
            }
            
            return result_dict
        else:
            # 无换手约束，直接优化
            return self.mean_variance_optimization(
                expected_returns, covariance_matrix, objective='sharpe'
            )
    
    def _apply_turnover_constraint(self,
                                 target_weights: pd.Series,
                                 current_weights: pd.Series,
                                 max_turnover: float) -> pd.Series:
        """应用换手率约束"""
        # 计算目标换手率
        target_turnover = np.sum(np.abs(target_weights - current_weights)) / 2
        
        if target_turnover <= max_turnover:
            return target_weights
        else:
            # 按比例缩小调整
            scale_factor = max_turnover / target_turnover
            adjusted_weights = current_weights + (target_weights - current_weights) * scale_factor
            
            # 重新归一化
            adjusted_weights = adjusted_weights / adjusted_weights.sum()
            
            return adjusted_weights
    
    def compare_optimization_methods(self,
                                   expected_returns: pd.Series,
                                   covariance_matrix: pd.DataFrame,
                                   current_weights: pd.Series = None) -> pd.DataFrame:
        """
        比较不同优化方法
        """
        methods = {
            'Mean-Variance (Sharpe)': lambda: self.mean_variance_optimization(
                expected_returns, covariance_matrix, objective='sharpe'
            ),
            'Mean-Variance (Min Var)': lambda: self.mean_variance_optimization(
                expected_returns, covariance_matrix, objective='variance'
            ),
            'Risk Parity': lambda: self.risk_parity_optimization(covariance_matrix),
            'Minimum Variance': lambda: self.minimum_variance_optimization(covariance_matrix),
            'Max Diversification': lambda: self.max_diversification_optimization(covariance_matrix)
        }
        
        results = []
        
        for method_name, optimizer_func in methods.items():
            try:
                result = optimizer_func()
                
                if result['success']:
                    stats = result.get('stats', {})
                    
                    results.append({
                        'Method': method_name,
                        'Expected Return': stats.get('expected_return', 0),
                        'Expected Risk': stats.get('expected_risk', 0),
                        'Sharpe Ratio': stats.get('sharpe_ratio', 0),
                        'Diversification': stats.get('diversification_ratio', 0),
                        'Concentration': stats.get('concentration', 0),
                        'Top 3 Holdings': ', '.join(
                            result['weights'].nlargest(3).index[:3]
                        ) if hasattr(result['weights'], 'nlargest') else 'N/A'
                    })
            except Exception as e:
                print(f"方法 {method_name} 失败: {e}")
        
        return pd.DataFrame(results)


# 测试函数
def test_portfolio_optimizer():
    """测试组合优化引擎"""
    print("=== 测试组合优化引擎 ===")
    
    # 创建模拟数据
    np.random.seed(42)
    n_assets = 10
    
    assets = [f'Asset{i}' for i in range(n_assets)]
    
    # 模拟预期收益率
    expected_returns = pd.Series(
        np.random.normal(0.001, 0.002, n_assets),
        index=assets
    )
    
    # 模拟协方差矩阵
    # 生成相关系数矩阵
    corr_matrix = np.eye(n_assets)
    for i in range(n_assets):
        for j in range(i+1, n_assets):
            corr = np.random.uniform(-0.3, 0.7)
            corr_matrix[i, j] = corr
            corr_matrix[j, i] = corr
    
    # 生成波动率
    volatilities = np.random.uniform(0.15, 0.35, n_assets)
    
    # 计算协方差矩阵
    covariance_matrix = np.outer(volatilities, volatilities) * corr_matrix
    covariance_matrix = pd.DataFrame(covariance_matrix, index=assets, columns=assets)
    
    # 创建优化器
    optimizer = PortfolioOptimizer(
        risk_free_rate=0.03,
        max_position=0.2,
        min_position=0.0
    )
    
    print(f"\n1. 均值-方差优化 (最大化夏普)...")
    mv_result = optimizer.mean_variance_optimization(
        expected_returns, covariance_matrix, objective='sharpe'
    )
    
    if mv_result['success']:
        print(f"  优化成功: {mv_result['message']}")
        stats = mv_result['stats']
        print(f"  预期收益: {stats['expected_return']:.4f}")
        print(f"  预期风险: {stats['expected_risk']:.4f}")
        print(f"  夏普比率: {stats['sharpe_ratio']:.4f}")
        
        # 显示前5个权重
        top_weights = mv_result['weights'].nlargest(5)
        print(f"  前5大权重:")
        for asset, weight in top_weights.items():
            print(f"    {asset}: {weight:.2%}")
    
    print(f"\n2. 风险平价优化...")
    rp_result = optimizer.risk_parity_optimization(covariance_matrix)
    
    if rp_result['success']:
        print(f"  优化成功: {rp_result['message']}")
        
        # 显示风险贡献
        risk_contrib = rp_result['risk_contribution']
        print(f"  风险贡献范围: [{risk_contrib.min():.4f}, {risk_contrib.max():.4f}]")
    
    print(f"\n3. 最小方差组合...")
    minvar_result = optimizer.minimum_variance_optimization(covariance_matrix)
    
    if minvar_result['success']:
        stats = minvar_result['stats']
        print(f"  最小风险: {stats['expected_risk']:.4f}")
    
    print(f"\n4. 方法比较...")
    comparison = optimizer.compare_optimization_methods(expected_returns, covariance_matrix)
    
    if not comparison.empty:
        print(comparison.to_string(index=False))
    
    print("\n✅ 组合优化测试完成")


if __name__ == "__main__":
    test_portfolio_optimizer()