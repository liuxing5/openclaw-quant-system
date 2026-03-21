#!/usr/bin/env python3
"""
端到端回测脚本
整合所有量化模块进行完整回测
"""
import sys
import os
import json

# 优先使用系统包
sys.path.insert(0, '/usr/lib/python3/dist-packages')
sys.path.append('/root/.openclaw/workspace/quant_system')
sys.path.append('/root/.openclaw/workspace/quant_system/real_factors')
sys.path.append('/root/.openclaw/workspace/quant_system/walkforward')

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

print("=" * 80)
print("端到端量化系统回测")
print("=" * 80)

# ============================================================================
# 1. 加载配置和数据
print("\n1. 📂 加载配置和数据...")

def load_backtest_data():
    """加载回测数据"""
    data_dir = '/root/.openclaw/workspace/quant_system/data/backtest'
    
    # 加载配置文件
    config_file = os.path.join(data_dir, 'backtest_config.json')
    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 加载价格数据
    price_file = os.path.join(data_dir, 'price_data.csv')
    price_data = pd.read_csv(price_file, index_col=0, parse_dates=True)
    
    # 加载成交量数据
    volume_file = os.path.join(data_dir, 'volume_data.csv')
    volume_data = pd.read_csv(volume_file, index_col=0, parse_dates=True)
    
    # 加载市场数据
    market_file = os.path.join(data_dir, 'market_data.csv')
    market_data = pd.read_csv(market_file, index_col=0, parse_dates=True)
    
    print(f"  ✓ 配置加载: {len(config.keys())}个配置项")
    print(f"  ✓ 价格数据: {price_data.shape[0]}天 × {price_data.shape[1]}只股票")
    print(f"  ✓ 成交量数据: {volume_data.shape[0]}天 × {volume_data.shape[1]}只股票")
    print(f"  ✓ 市场数据: {market_data.shape[0]}天 × {market_data.shape[1]}列")
    
    return config, price_data, volume_data, market_data

# ============================================================================
# 2. 初始化量化模块
print("\n2. 🔧 初始化量化模块...")

def initialize_quant_modules():
    """初始化所有量化模块"""
    modules = {}
    
    try:
        # 真实因子管理器
        from real_factor_manager import RealFactorManager
        modules['factor_manager'] = RealFactorManager()
        print("  ✓ 真实因子管理器初始化成功")
        print(f"    可用因子: {len(modules['factor_manager'].factors)}个")
        
    except Exception as e:
        print(f"  ✗ 因子管理器初始化失败: {e}")
        modules['factor_manager'] = None
    
    try:
        # 市场状态识别器
        from regime_detection import MarketRegimeDetector
        modules['regime_detector'] = MarketRegimeDetector(n_regimes=3)
        print("  ✓ 市场状态识别器初始化成功")
        
    except Exception as e:
        print(f"  ✗ 市场状态识别器初始化失败: {e}")
        modules['regime_detector'] = None
    
    try:
        # 多因子回归模型
        from multi_factor_regression import MultiFactorRegression
        modules['factor_regression'] = MultiFactorRegression()
        print("  ✓ 多因子回归模型初始化成功")
        
    except Exception as e:
        print(f"  ✗ 多因子回归模型初始化失败: {e}")
        modules['factor_regression'] = None
    
    try:
        # Alpha预测器
        from alpha_predictor import AlphaPredictor
        modules['alpha_predictor'] = AlphaPredictor(model_type='gbr')
        print("  ✓ Alpha预测器初始化成功")
        
    except Exception as e:
        print(f"  ✗ Alpha预测器初始化失败: {e}")
        modules['alpha_predictor'] = None
    
    try:
        # 组合优化器
        from portfolio_optimizer import PortfolioOptimizer
        modules['portfolio_optimizer'] = PortfolioOptimizer(
            risk_free_rate=0.03,
            max_position=0.1,
            min_position=0.0
        )
        print("  ✓ 组合优化器初始化成功")
        
    except Exception as e:
        print(f"  ✗ 组合优化器初始化失败: {e}")
        modules['portfolio_optimizer'] = None
    
    try:
        # Walk-forward回测器
        from walkforward_backtester import WalkForwardBacktester, WalkForwardConfig
        wf_config = WalkForwardConfig(
            train_years=2,
            validation_months=3,
            test_months=6,
            step_months=3
        )
        modules['walkforward_tester'] = WalkForwardBacktester(wf_config)
        print("  ✓ Walk-forward回测器初始化成功")
        
    except Exception as e:
        print(f"  ✗ Walk-forward回测器初始化失败: {e}")
        modules['walkforward_tester'] = None
    
    return modules

# ============================================================================
# 3. 执行因子计算
print("\n3. 📊 执行因子计算...")

def calculate_factors(factor_manager, price_data, volume_data, sample_stocks=None):
    """计算因子值"""
    if factor_manager is None:
        print("  ⚠ 因子管理器不可用，跳过因子计算")
        return None
    
    if sample_stocks is None:
        # 取前10只股票作为样本
        sample_stocks = price_data.columns[:10].tolist()
    
    print(f"  计算因子: {len(sample_stocks)}只样本股票")
    
    factor_results = {}
    for stock in sample_stocks[:5]:  # 限制为5只股票以加快速度
        try:
            # 准备数据
            stock_data = pd.DataFrame({
                'open': price_data[stock] * 0.99,  # 简化：使用价格推算开盘价
                'high': price_data[stock] * 1.02,
                'low': price_data[stock] * 0.98,
                'close': price_data[stock],
                'volume': volume_data[stock] if stock in volume_data.columns else 1000000
            }, index=price_data.index)
            
            # 计算技术因子
            momentum = factor_manager.calculate_factor('momentum_1m', stock_data)
            volatility = factor_manager.calculate_factor('volatility_20d', stock_data)
            rsi = factor_manager.calculate_factor('rsi_14', stock_data)
            
            factor_results[stock] = {
                'momentum': momentum,
                'volatility': volatility,
                'rsi': rsi
            }
            
        except Exception as e:
            print(f"    ⚠ 股票{stock}因子计算失败: {e}")
    
    print(f"  ✓ 因子计算完成: {len(factor_results)}只股票")
    return factor_results

# ============================================================================
# 4. 执行市场状态识别
print("\n4. 🌍 执行市场状态识别...")

def detect_market_regimes(regime_detector, market_data):
    """识别市场状态"""
    if regime_detector is None:
        print("  ⚠ 市场状态识别器不可用，跳过")
        return None
    
    try:
        # 计算市场收益率
        market_returns = market_data['close'].pct_change().dropna()
        
        # 识别市场状态
        regimes = regime_detector.detect_regimes_gmm(market_returns)
        
        # 分析状态统计
        regime_stats = regime_detector.regime_stats
        
        print(f"  ✓ 市场状态识别完成")
        for regime_id, stats in regime_stats.items():
            print(f"    状态{regime_id}: {stats['label']} - {stats['count']}天 ({stats['percentage']:.1f}%)")
        
        return regimes, regime_stats
        
    except Exception as e:
        print(f"  ✗ 市场状态识别失败: {e}")
        return None, None

# ============================================================================
# 5. 执行Alpha预测
print("\n5. 🤖 执行Alpha预测...")

def run_alpha_prediction(alpha_predictor, price_data, volume_data, sample_stock):
    """运行Alpha预测"""
    if alpha_predictor is None:
        print("  ⚠ Alpha预测器不可用，跳过")
        return None
    
    try:
        # 使用第一只股票作为示例
        stock = sample_stock if sample_stock in price_data.columns else price_data.columns[0]
        
        # 准备数据
        price_series = price_data[stock]
        volume_series = volume_data[stock] if stock in volume_data.columns else pd.Series(1000000, index=price_data.index)
        
        # 创建特征
        features = alpha_predictor.create_features(
            price_data=price_series,
            volume_data=volume_series,
            fundamental_data=None,
            market_data=None
        )
        
        # 创建目标（预测未来5日收益）
        target = alpha_predictor.create_target(price_series, horizon=5)
        
        # 对齐数据
        common_idx = features.index.intersection(target.index)
        if len(common_idx) < 100:
            print(f"  ⚠ 数据不足: {len(common_idx)}个样本")
            return None
        
        features = features.loc[common_idx]
        target = target.loc[common_idx]
        
        # 训练模型
        print(f"  训练Alpha预测模型 ({len(features)}个样本)...")
        training_result = alpha_predictor.train(features, target, early_stopping=False)
        
        print(f"  ✓ Alpha预测训练完成")
        print(f"    测试集R²: {training_result.get('test_r2', 'N/A')}")
        print(f"    测试集IC: {training_result.get('test_ic', 0):.4f}")
        
        return training_result
        
    except Exception as e:
        print(f"  ✗ Alpha预测失败: {e}")
        return None

# ============================================================================
# 6. 执行组合优化
print("\n6. ⚖️ 执行组合优化...")

def run_portfolio_optimization(portfolio_optimizer, price_data, sample_stocks):
    """运行组合优化"""
    if portfolio_optimizer is None:
        print("  ⚠ 组合优化器不可用，跳过")
        return None
    
    try:
        # 选择10只股票
        stocks = sample_stocks[:10] if len(sample_stocks) >= 10 else sample_stocks
        
        # 计算收益率
        returns_data = price_data[stocks].pct_change().dropna()
        
        # 计算预期收益率（简单均值）
        expected_returns = returns_data.mean()
        
        # 计算协方差矩阵
        covariance_matrix = returns_data.cov()
        
        print(f"  优化组合: {len(stocks)}只股票")
        
        # 运行均值-方差优化
        mv_result = portfolio_optimizer.mean_variance_optimization(
            expected_returns, covariance_matrix, objective='sharpe'
        )
        
        if mv_result['success']:
            stats = mv_result['stats']
            print(f"  ✓ 组合优化完成")
            print(f"    预期收益: {stats['expected_return']:.4f}")
            print(f"    预期风险: {stats['expected_risk']:.4f}")
            print(f"    夏普比率: {stats['sharpe_ratio']:.4f}")
            
            # 显示权重分布
            weights = mv_result['weights']
            top_3 = weights.nlargest(3)
            print(f"    前3大权重:")
            for stock, weight in top_3.items():
                print(f"      {stock}: {weight:.2%}")
        
        return mv_result
        
    except Exception as e:
        print(f"  ✗ 组合优化失败: {e}")
        return None

# ============================================================================
# 7. 执行Walk-forward回测
print("\n7. 🔄 执行Walk-forward回测...")

def run_walkforward_backtest(walkforward_tester, config):
    """运行Walk-forward回测"""
    if walkforward_tester is None:
        print("  ⚠ Walk-forward回测器不可用，跳过")
        return None
    
    try:
        # 创建期间
        periods = walkforward_tester.create_walkforward_periods(
            config['data']['period']['start_date'],
            config['data']['period']['end_date']
        )
        
        print(f"  ✓ Walk-forward期间划分: {len(periods)}个期间")
        
        if periods:
            first_period = periods[0]
            print(f"    第一期间: 训练{first_period.train_start.date()}~{first_period.train_end.date()}")
            print(f"              测试{first_period.test_start.date()}~{first_period.test_end.date()}")
        
        return periods
        
    except Exception as e:
        print(f"  ✗ Walk-forward回测失败: {e}")
        return None

# ============================================================================
# 8. 生成回测报告
print("\n8. 📈 生成回测报告...")

def generate_backtest_report(results):
    """生成回测报告"""
    report = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'summary': {
            'modules_initialized': 0,
            'modules_failed': 0,
            'tests_completed': 0,
            'tests_failed': 0
        },
        'module_status': {},
        'test_results': {},
        'recommendations': []
    }
    
    # 统计模块状态
    for module_name, module_result in results.items():
        if module_result is not None:
            report['module_status'][module_name] = '✓ 成功'
            report['summary']['modules_initialized'] += 1
        else:
            report['module_status'][module_name] = '✗ 失败'
            report['summary']['modules_failed'] += 1
    
    # 添加建议
    if report['summary']['modules_initialized'] >= 4:
        report['recommendations'].append("✅ 系统核心模块运行正常，建议进行实盘模拟测试")
    else:
        report['recommendations'].append("⚠ 部分模块初始化失败，建议检查依赖和环境")
    
    if results.get('factor_results') and len(results['factor_results']) >= 3:
        report['recommendations'].append("✅ 因子计算成功，建议扩展更多因子")
    
    if results.get('alpha_result') and results['alpha_result'].get('test_ic', 0) > 0.05:
        report['recommendations'].append("✅ Alpha预测IC>0.05，模型预测能力良好")
    elif results.get('alpha_result'):
        report['recommendations'].append("⚠ Alpha预测IC较低，建议优化特征工程")
    
    return report

# ============================================================================
# 主执行
if __name__ == "__main__":
    try:
        # 加载数据
        config, price_data, volume_data, market_data = load_backtest_data()
        
        # 初始化模块
        modules = initialize_quant_modules()
        
        # 执行各项测试
        results = {}
        
        # 因子计算
        sample_stocks = price_data.columns[:10].tolist()
        results['factor_results'] = calculate_factors(
            modules['factor_manager'], price_data, volume_data, sample_stocks
        )
        
        # 市场状态识别
        results['regimes'], results['regime_stats'] = detect_market_regimes(
            modules['regime_detector'], market_data
        )
        
        # Alpha预测
        results['alpha_result'] = run_alpha_prediction(
            modules['alpha_predictor'], price_data, volume_data, sample_stocks[0]
        )
        
        # 组合优化
        results['portfolio_result'] = run_portfolio_optimization(
            modules['portfolio_optimizer'], price_data, sample_stocks
        )
        
        # Walk-forward回测
        results['walkforward_periods'] = run_walkforward_backtest(
            modules['walkforward_tester'], config
        )
        
        # 生成报告
        report = generate_backtest_report(results)
        
        # 输出报告
        print("\n" + "=" * 80)
        print("端到端回测完成报告")
        print("=" * 80)
        
        print(f"\n📅 回测时间: {report['timestamp']}")
        print(f"📊 模块状态: {report['summary']['modules_initialized']}成功 / {report['summary']['modules_failed']}失败")
        
        print("\n📋 模块详情:")
        for module, status in report['module_status'].items():
            print(f"  {module:25} {status}")
        
        print("\n💡 建议:")
        for i, recommendation in enumerate(report['recommendations'], 1):
            print(f"  {i}. {recommendation}")
        
        print("\n🎯 关键结果:")
        if results.get('factor_results'):
            print(f"  • 因子计算: {len(results['factor_results'])}只股票成功")
        
        if results.get('regime_stats'):
            for regime_id, stats in results['regime_stats'].items():
                print(f"  • 市场状态{regime_id}: {stats['label']} ({stats['percentage']:.1f}%)")
        
        if results.get('alpha_result'):
            ic = results['alpha_result'].get('test_ic', 0)
            r2 = results['alpha_result'].get('test_r2', 'N/A')
            print(f"  • Alpha预测: IC={ic:.4f}, R²={r2}")
        
        if results.get('portfolio_result') and results['portfolio_result']['success']:
            stats = results['portfolio_result']['stats']
            print(f"  • 组合优化: 收益={stats['expected_return']:.4f}, 风险={stats['expected_risk']:.4f}, 夏普={stats['sharpe_ratio']:.4f}")
        
        if results.get('walkforward_periods'):
            print(f"  • Walk-forward: {len(results['walkforward_periods'])}个回测期间")
        
        print("\n🚀 下一步行动:")
        print("  1. 扩展数据: 使用更多真实股票数据")
        print("  2. 优化参数: 调整模型超参数")
        print("  3. 完整回测: 运行完整的Walk-forward回测")
        print("  4. 风险分析: 深入分析组合风险特征")
        
        print("\n" + "=" * 80)
        print("端到端回测执行完成")
        print("=" * 80)
        
    except Exception as e:
        print(f"端到端回测失败: {e}")
        import traceback
        traceback.print_exc()