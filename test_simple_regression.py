#!/usr/bin/env python3
"""
简化多因子回归测试 - 不使用statsmodels
"""
import sys
# 优先使用系统包
sys.path.insert(0, '/usr/lib/python3/dist-packages')
sys.path.insert(0, '/usr/local/lib/python3.12/dist-packages')

import numpy as np
import pandas as pd

print("测试简化回归...")
print(f"NumPy版本: {np.__version__}")
print(f"Pandas版本: {pd.__version__}")

# 简单的OLS回归实现
def simple_ols(X, y):
    """简单OLS回归，不使用statsmodels"""
    # 添加常数项
    X_with_const = np.column_stack([np.ones(len(X)), X])
    
    # 正规方程: β = (X'X)^(-1) X'y
    XTX = X_with_const.T @ X_with_const
    XTy = X_with_const.T @ y
    
    try:
        beta = np.linalg.inv(XTX) @ XTy
    except np.linalg.LinAlgError:
        # 奇异矩阵，使用伪逆
        beta = np.linalg.pinv(XTX) @ XTy
    
    return beta

# 测试数据
np.random.seed(42)
n_samples = 100
n_features = 3

X = np.random.randn(n_samples, n_features)
true_beta = np.array([1.5, -2.0, 0.5])
y = X @ true_beta + np.random.randn(n_samples) * 0.1 + 2.0  # 有截距

# 运行回归
beta_hat = simple_ols(X, y)
print(f"\n真实系数: {true_beta}")
print(f"估计系数: {beta_hat[1:]}")  # 跳过截距
print(f"截距: {beta_hat[0]:.4f}")

# 计算R²
y_pred = beta_hat[0] + X @ beta_hat[1:]
ss_res = np.sum((y - y_pred) ** 2)
ss_tot = np.sum((y - np.mean(y)) ** 2)
r_squared = 1 - ss_res / ss_tot
print(f"R²: {r_squared:.4f}")

print("\n✅ 简化回归测试成功")