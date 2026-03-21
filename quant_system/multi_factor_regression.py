#!/usr/bin/env python3
"""
多因子回归模型 - 替代IC动态加权
专业方法: 横截面回归 + 因子收益率分解 + 风险暴露控制
解决"半拍脑袋"权重问题
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
import warnings
import statsmodels.api as sm
from scipy import stats
warnings.filterwarnings('ignore')

try:
    from real_factors.real_factor_manager import RealFactorManager
    FACTOR_MANAGER_AVAILABLE = True
except ImportError:
    FACTOR_MANAGER_AVAILABLE = False


class MultiFactorRegression:
    """多因子回归模型（Fama-French/Barra风格）"""
    
    def __init__(self, 
                 factor_ids: List[str] = None,
                 risk_free_rate: float = 0.03,
                 min_obs: int = 100,
                 significance_level: float = 0.05):
        """
        初始化多因子回归模型
        
        Args:
            factor_ids: 使用的因子ID列表
            risk_free_rate: 无风险利率（年化）
            min_obs: 最小观测值数量
            significance_level: 显著性水平
        """
        self.factor_ids = factor_ids or []
        self.risk_free_rate = risk_free_rate
        self.min_obs = min_obs
        self.significance_level = significance_level
        
        # 因子管理器
        if FACTOR_MANAGER_AVAILABLE:
            self.factor_manager = RealFactorManager()
        else:
            self.factor_manager = None
            print("警告: 因子管理器不可用")
        
        # 回归结果存储
        self.regression_results = {}
        self.factor_returns = pd.DataFrame()
        self.factor_exposures = pd.DataFrame()
        self.residual_returns = pd.DataFrame()
        
        # 模型统计
        self.model_stats = {
            'r_squared': [],
            'adj_r_squared': [],
            'f_statistic': [],
            'f_pvalue': []
        }
    
    def prepare_regression_data(self,
                               stock_returns: pd.DataFrame,
                               factor_values: pd.DataFrame,
                               market_returns: pd.Series = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        准备回归数据
        
        Args:
            stock_returns: 股票收益率矩阵 (股票×日期)
            factor_values: 因子值矩阵 (股票×因子×日期)
            market_returns: 市场收益率序列
            
        Returns:
            X: 解释变量 (因子值 + 可选市场因子)
            y: 被解释变量 (超额收益)
        """
        # 确保数据对齐
        common_dates = stock_returns.index.intersection(factor_values.index)
        common_stocks = stock_returns.columns.intersection(factor_values.columns)
        
        if len(common_dates) < self.min_obs or len(common_stocks) < 10:
            raise ValueError(f"数据不足: {len(common_dates)}个日期, {len(common_stocks)}只股票")
        
        # 计算超额收益
        if market_returns is not None:
            # 市场模型超额收益
            excess_returns = stock_returns.sub(market_returns, axis=0)
        else:
            # 简单超额收益（减去均值）
            excess_returns = stock_returns.sub(stock_returns.mean(axis=1), axis=0)
        
        # 准备截面数据
        X_list = []
        y_list = []
        
        for date in common_dates:
            # 获取该日期所有股票的因子值
            date_factor_data = factor_values.loc[date]
            date_returns = excess_returns.loc[date]
            
            # 对齐股票
            common = date_factor_data.index.intersection(date_returns.index)
            if len(common) < 10:
                continue
            
            X_date = date_factor_data.loc[common]
            y_date = date_returns.loc[common]
            
            # 标准化因子值（截面标准化）
            X_normalized = (X_date - X_date.mean()) / X_date.std()
            X_normalized = X_normalized.fillna(0)
            
            X_list.append(X_normalized)
            y_list.append(y_date)
        
        if not X_list:
            raise ValueError("无有效回归数据")
        
        X_full = pd.concat(X_list, keys=common_dates[:len(X_list)], names=['date', 'stock'])
        y_full = pd.concat(y_list, keys=common_dates[:len(y_list)], names=['date', 'stock'])
        
        return X_full, y_full
    
    def run_cross_sectional_regression(self,
                                      X: pd.DataFrame,
                                      y: pd.Series,
                                      date: pd.Timestamp = None) -> Dict[str, Any]:
        """
        运行横截面回归
        
        Returns:
            回归结果字典
        """
        # 添加常数项
        X_with_const = sm.add_constant(X)
        
        # 运行OLS回归
        model = sm.OLS(y, X_with_const)
        results = model.fit()
        
        # 提取因子收益率（系数）
        factor_returns = results.params
        factor_tvalues = results.tvalues
        factor_pvalues = results.pvalues
        
        # 计算因子显著性
        significant_factors = []
        for factor in factor_returns.index:
            if factor == 'const':
                continue
            pvalue = factor_pvalues.get(factor, 1.0)
            tvalue = factor_tvalues.get(factor, 0.0)
            if pvalue < self.significance_level and abs(tvalue) > 1.96:
                significant_factors.append(factor)
        
        # 计算残差收益
        residuals = results.resid
        
        # 计算模型诊断统计
        diagnosis = {
            'r_squared': results.rsquared,
            'adj_r_squared': results.rsquared_adj,
            'f_statistic': results.fvalue,
            'f_pvalue': results.f_pvalue,
            'durbin_watson': sm.stats.stattools.durbin_watson(residuals),
            'jarque_bera': sm.stats.stattools.jarque_bera(residuals),
            'omnibus': sm.stats.stattools.omni_normtest(residuals)
        }
        
        return {
            'date': date,
            'factor_returns': factor_returns,
            'factor_tvalues': factor_tvalues,
            'factor_pvalues': factor_pvalues,
            'significant_factors': significant_factors,
            'residuals': residuals,
            'diagnosis': diagnosis,
            'model_summary': str(results.summary())
        }
    
    def run_time_series_regression(self,
                                  stock_returns: pd.Series,
                                  factor_returns: pd.DataFrame) -> Dict[str, Any]:
        """
        运行时间序列回归（计算因子暴露）
        
        Args:
            stock_returns: 单只股票的时间序列收益
            factor_returns: 因子收益率时间序列
            
        Returns:
            回归结果
        """
        # 对齐数据
        common_idx = stock_returns.index.intersection(factor_returns.index)
        if len(common_idx) < self.min_obs:
            return {'error': '数据不足'}
        
        y = stock_returns.loc[common_idx]
        X = factor_returns.loc[common_idx]
        
        # 添加常数项（Alpha）
        X_with_const = sm.add_constant(X)
        
        model = sm.OLS(y, X_with_const)
        results = model.fit()
        
        # 提取因子暴露（Beta）
        factor_exposures = results.params.drop('const')
        factor_exposure_tvalues = results.tvalues.drop('const')
        
        # Alpha（超额收益）
        alpha = results.params.get('const', 0.0)
        alpha_tvalue = results.tvalues.get('const', 0.0)
        alpha_significant = abs(alpha_tvalue) > 1.96
        
        # 计算风险贡献
        total_variance = np.var(y)
        if total_variance > 0:
            # 每个因子的风险贡献
            risk_contributions = {}
            for factor in factor_exposures.index:
                factor_var = np.var(X[factor])
                risk_contrib = (factor_exposures[factor] ** 2) * factor_var / total_variance
                risk_contributions[factor] = risk_contrib
        else:
            risk_contributions = {}
        
        return {
            'alpha': alpha,
            'alpha_significant': alpha_significant,
            'alpha_tvalue': alpha_tvalue,
            'factor_exposures': factor_exposures,
            'factor_exposure_tvalues': factor_exposure_tvalues,
            'r_squared': results.rsquared,
            'adj_r_squared': results.rsquared_adj,
            'residual_std': np.std(results.resid),
            'risk_contributions': risk_contributions,
            'model_summary': str(results.summary())
        }
    
    def calculate_factor_risk_model(self,
                                   factor_returns: pd.DataFrame) -> Dict[str, Any]:
        """
        计算因子风险模型（协方差矩阵、风险贡献等）
        """
        # 计算因子收益率协方差矩阵
        cov_matrix = factor_returns.cov()
        
        # 特征值分解（检查多重共线性）
        eigenvalues, eigenvectors = np.linalg.eig(cov_matrix)
        
        # 条件数（多重共线性指标）
        condition_number = np.max(eigenvalues) / np.min(eigenvalues) if np.min(eigenvalues) > 0 else np.inf
        
        # 主成分分析（PCA）
        from sklearn.decomposition import PCA
        pca = PCA(n_components=min(5, len(cov_matrix.columns)))
        pca.fit(factor_returns)
        
        # 方差解释比例
        explained_variance_ratio = pca.explained_variance_ratio_
        
        # 风险贡献分析
        total_risk = np.trace(cov_matrix)
        risk_contributions = {}
        for i, factor in enumerate(cov_matrix.columns):
            factor_risk = cov_matrix.iloc[i, i]
            risk_contributions[factor] = factor_risk / total_risk if total_risk > 0 else 0
        
        return {
            'covariance_matrix': cov_matrix,
            'correlation_matrix': factor_returns.corr(),
            'eigenvalues': eigenvalues,
            'condition_number': condition_number,
            'pca_explained_variance': explained_variance_ratio,
            'risk_contributions': risk_contributions,
            'total_risk': total_risk
        }
    
    def optimize_factor_weights(self,
                               expected_returns: pd.Series,
                               cov_matrix: pd.DataFrame,
                               constraints: Dict[str, Any] = None) -> pd.Series:
        """
        优化因子权重（均值-方差优化）
        
        Args:
            expected_returns: 预期因子收益率
            cov_matrix: 因子协方差矩阵
            constraints: 优化约束
            
        Returns:
            优化后的因子权重
        """
        from scipy.optimize import minimize
        
        n_factors = len(expected_returns)
        
        # 默认约束
        if constraints is None:
            constraints = {
                'long_only': True,
                'sum_to_one': True,
                'max_weight': 0.3,
                'min_weight': 0.0
            }
        
        # 目标函数：最大化夏普比率
        def objective(weights):
            port_return = np.dot(weights, expected_returns)
            port_risk = np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights)))
            sharpe = port_return / port_risk if port_risk > 0 else 0
            return -sharpe  # 最小化负夏普
        
        # 约束条件
        cons = []
        
        if constraints.get('sum_to_one', True):
            cons.append({'type': 'eq', 'fun': lambda w: np.sum(w) - 1})
        
        if constraints.get('long_only', True):
            bounds = [(constraints.get('min_weight', 0.0), 
                      constraints.get('max_weight', 1.0)) for _ in range(n_factors)]
        else:
            bounds = [(-constraints.get('max_weight', 0.3), 
                      constraints.get('max_weight', 0.3)) for _ in range(n_factors)]
        
        # 初始权重（等权重）
        init_weights = np.ones(n_factors) / n_factors
        
        # 优化
        result = minimize(objective, init_weights, 
                         bounds=bounds, constraints=cons,
                         method='SLSQP', options={'maxiter': 1000})
        
        if result.success:
            optimized_weights = pd.Series(result.x, index=expected_returns.index)
            
            # 计算优化后组合统计
            opt_return = np.dot(optimized_weights, expected_returns)
            opt_risk = np.sqrt(np.dot(optimized_weights.T, np.dot(cov_matrix, optimized_weights)))
            opt_sharpe = opt_return / opt_risk if opt_risk > 0 else 0
            
            return {
                'weights': optimized_weights,
                'expected_return': opt_return,
                'expected_risk': opt_risk,
                'sharpe_ratio': opt_sharpe,
                'optimization_success': True,
                'message': result.message
            }
        else:
            # 优化失败，返回等权重
            equal_weights = pd.Series(np.ones(n_factors) / n_factors, index=expected_returns.index)
            eq_return = np.dot(equal_weights, expected_returns)
            eq_risk = np.sqrt(np.dot(equal_weights.T, np.dot(cov_matrix, equal_weights)))
            eq_sharpe = eq_return / eq_risk if eq_risk > 0 else 0
            
            return {
                'weights': equal_weights,
                'expected_return': eq_return,
                'expected_risk': eq_risk,
                'sharpe_ratio': eq_sharpe,
                'optimization_success': False,
                'message': result.message
            }


# 测试函数
def test_multi_factor_regression():
    """测试多因子回归模型"""
    print("=== 测试多因子回归模型 ===")
    
    # 创建模拟数据
    n_dates = 100
    n_stocks = 50
    n_factors = 5
    
    dates = pd.date_range('2023-01-01', periods=n_dates, freq='D')
    stocks = [f'Stock{i:03d}' for i in range(n_stocks)]
    factors = [f'Factor{i}' for i in range(n_factors)]
    
    # 模拟因子值
    np.random.seed(42)
    factor_values = pd.DataFrame(
        np.random.randn(n_dates * n_stocks, n_factors).cumsum(axis=0),
        index=pd.MultiIndex.from_product([dates, stocks], names=['date', 'stock']),
        columns=factors
    )
    
    # 模拟股票收益（受因子影响）
    true_betas = np.random.randn(n_stocks, n_factors)
    stock_returns = pd.DataFrame(index=dates, columns=stocks)
    
    for i, stock in enumerate(stocks):
        # 每只股票的真实因子暴露
        beta = true_betas[i]
        for date in dates:
            factor_returns = np.random.randn(n_factors) * 0.01
            stock_return = np.dot(beta, factor_returns) + np.random.randn() * 0.02
            stock_returns.loc[date, stock] = stock_return
    
    # 创建回归模型
    model = MultiFactorRegression(factor_ids=factors)
    
    print(f"创建模型: {len(factors)}个因子")
    
    # 准备回归数据
    try:
        X, y = model.prepare_regression_data(stock_returns, factor_values)
        print(f"回归数据: X形状={X.shape}, y形状={y.shape}")
        
        # 运行横截面回归（取第一个截面）
        first_date = dates[0]
        X_first = X.xs(first_date, level='date')
        y_first = y.xs(first_date, level='date')
        
        if len(X_first) > 10:
            result = model.run_cross_sectional_regression(X_first, y_first, first_date)
            print(f"\n横截面回归结果 ({first_date.date()}):")
            print(f"  R²: {result['diagnosis']['r_squared']:.4f}")
            print(f"  显著因子: {len(result['significant_factors'])}个")
            
            if result['significant_factors']:
                print("  显著因子列表:")
                for factor in result['significant_factors'][:3]:
                    ret = result['factor_returns'][factor]
                    pval = result['factor_pvalues'][factor]
                    print(f"    {factor}: 收益率={ret:.6f}, p值={pval:.4f}")
        
        # 计算因子风险模型
        print(f"\n因子风险模型:")
        factor_returns_sim = pd.DataFrame(
            np.random.randn(n_dates, n_factors).cumsum(axis=0) * 0.01,
            index=dates, columns=factors
        )
        
        risk_model = model.calculate_factor_risk_model(factor_returns_sim)
        print(f"  条件数: {risk_model['condition_number']:.2f}")
        print(f"  PCA解释方差: {risk_model['pca_explained_variance'][0]:.1%} (第一主成分)")
        
        # 测试权重优化
        print(f"\n因子权重优化:")
        expected_returns = pd.Series(np.random.randn(n_factors) * 0.001, index=factors)
        cov_matrix = risk_model['covariance_matrix']
        
        opt_result = model.optimize_factor_weights(expected_returns, cov_matrix)
        
        if opt_result['optimization_success']:
            print(f"  优化成功: 夏普={opt_result['sharpe_ratio']:.4f}")
            print(f"  最优权重:")
            for factor, weight in opt_result['weights'].items():
                if abs(weight) > 0.01:
                    print(f"    {factor}: {weight:.2%}")
        else:
            print(f"  优化失败: {opt_result['message']}")
            print(f"  等权重夏普: {opt_result['sharpe_ratio']:.4f}")
        
        print("\n✅ 多因子回归模型测试完成")
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_multi_factor_regression()