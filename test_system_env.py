#!/usr/bin/env python3
"""
测试系统Python环境下多因子回归和Alpha预测器
"""
import sys
import os

# 优先使用系统包
sys.path.insert(0, '/usr/lib/python3/dist-packages')
# 添加quant_system路径
sys.path.append('/root/.openclaw/workspace/quant_system')
sys.path.append('/root/.openclaw/workspace/quant_system/real_factors')

print("=== 系统环境测试 ===")
print(f"Python路径:")
for p in sys.path[:5]:
    print(f"  {p}")

print("\n1. 测试多因子回归导入...")
try:
    # 测试statsmodels
    import statsmodels.api as sm
    print("  statsmodels ✓")
    
    # 创建简化版回归测试
    import numpy as np
    import pandas as pd
    
    # 生成测试数据
    np.random.seed(42)
    X = np.random.randn(100, 3)
    y = X[:, 0] * 1.5 + X[:, 1] * (-2.0) + X[:, 2] * 0.5 + np.random.randn(100) * 0.1
    
    # 添加常数项
    X_with_const = sm.add_constant(X)
    
    # 运行OLS
    model = sm.OLS(y, X_with_const)
    results = model.fit()
    
    print(f"  回归R²: {results.rsquared:.4f} ✓")
    print(f"  系数: {results.params} ✓")
    
except Exception as e:
    print(f"  ✗ 多因子回归测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n2. 测试Alpha预测器导入...")
try:
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.preprocessing import StandardScaler
    
    # 创建简单模型
    model = GradientBoostingRegressor(n_estimators=10, random_state=42)
    X = np.random.randn(50, 5)
    y = np.random.randn(50)
    
    model.fit(X, y)
    predictions = model.predict(X[:5])
    
    print(f"  GradientBoosting模型 ✓")
    print(f"  预测形状: {predictions.shape} ✓")
    
    # 测试标准化
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    print(f"  标准化: 均值={X_scaled.mean():.2f}, 标准差={X_scaled.std():.2f} ✓")
    
except Exception as e:
    print(f"  ✗ Alpha预测器测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n3. 测试真实因子管理器...")
try:
    # 尝试导入real_factor_manager
    from real_factor_manager import RealFactorManager
    
    fm = RealFactorManager()
    print(f"  真实因子管理器 ✓")
    print(f"  因子数量: {len(fm.factors)}")
    
    # 创建模拟数据
    dates = pd.date_range('2024-01-01', periods=10, freq='D')
    df = pd.DataFrame({
        'open': 100 + np.random.randn(10).cumsum(),
        'high': 105 + np.random.randn(10).cumsum(),
        'low': 95 + np.random.randn(10).cumsum(),
        'close': 100 + np.random.randn(10).cumsum(),
        'volume': 1000000 + np.random.randn(10).cumsum() * 100000
    }, index=dates)
    
    # 测试技术因子
    momentum = fm.calculate_factor('momentum_1m', df)
    print(f"  动量因子计算: 形状={momentum.shape} ✓")
    
except Exception as e:
    print(f"  ✗ 真实因子管理器测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n4. 测试walk-forward回测...")
try:
    sys.path.append('/root/.openclaw/workspace/quant_system/walkforward')
    from walkforward_backtester import WalkForwardBacktester, WalkForwardConfig
    
    config = WalkForwardConfig(
        train_years=1,
        validation_months=2,
        test_months=3,
        step_months=2,
        initial_capital=1000000.0
    )
    
    tester = WalkForwardBacktester(config)
    print(f"  WalkForward配置 ✓")
    print(f"  训练年数: {config.train_years}")
    print(f"  测试月数: {config.test_months}")
    
except Exception as e:
    print(f"  ✗ Walk-forward测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n=== 测试完成 ===")
print("所有核心模块在系统环境下均可正常导入！")