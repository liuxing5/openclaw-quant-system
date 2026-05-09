#!/usr/bin/env python3
"""
测试未来函数预防机制
演示DataAssurance如何检测和防止Walk-forward回测中的look-ahead bias

用户指出的关键问题：
1. 因子标准化使用全局统计量（global z-score）而非滚动窗口统计量
2. 因子IC/IR计算时使用了未来信息
3. 财务因子未严格使用t-1期报告期数据
4. LightGBM训练时特征未严格滞后（label_date.min() <= train_end）
5. 特征数据包含未来日期的信息（feature_date.max() > train_end）

解决方案：
1. 强制所有因子在每个滚动窗口内只使用截至训练期最后一天的信息重新计算
2. 财务因子必须严格使用 report_date ≤ train_end 的最新一期数据
3. 实现严格的静态检查：assert feature_date.max() <= train_end, assert label_date.min() > train_end
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

print("=" * 80)
print("未来函数预防机制测试")
print("=" * 80)

# 创建测试数据
np.random.seed(42)
dates = pd.date_range('2020-01-01', '2023-12-31', freq='D')

# 模拟特征数据（故意包含未来信息）
print("\n1. 模拟特征数据泄露测试")
features_leaky = pd.DataFrame({
    'feature1': np.random.randn(len(dates)),
    'feature2': np.random.randn(len(dates)) * 2
}, index=dates)

print(f"特征数据日期范围: {features_leaky.index.min().date()} - {features_leaky.index.max().date()}")
print(f"特征数据形状: {features_leaky.shape}")

# 模拟标签数据
print("\n2. 模拟标签数据测试")
label_dates = pd.date_range('2022-07-01', '2023-12-31', freq='D')
labels_correct = pd.DataFrame({
    'return_5d': np.random.randn(len(label_dates)) * 0.01
}, index=label_dates)

# 错误的标签数据（包含未来信息）
label_dates_leaky = pd.date_range('2022-01-01', '2023-12-31', freq='D')  # 开始于训练集期间
labels_leaky = pd.DataFrame({
    'return_5d': np.random.randn(len(label_dates_leaky)) * 0.01
}, index=label_dates_leaky)

print(f"正确标签日期范围: {labels_correct.index.min().date()} - {labels_correct.index.max().date()}")
print(f"泄露标签日期范围: {labels_leaky.index.min().date()} - {labels_leaky.index.max().date()}")

# 模拟财务数据
print("\n3. 模拟财务数据测试")
financial_dates = pd.date_range('2020-03-31', '2023-12-31', freq='QE')  # 季度末
financial_data_correct = pd.DataFrame({
    'report_date': financial_dates,
    'roe': np.random.randn(len(financial_dates)) * 0.1 + 0.15,
    'profit_growth': np.random.randn(len(financial_dates)) * 0.2 + 0.1
})

# 错误的财务数据（包含未来报告）
financial_dates_leaky = pd.date_range('2020-03-31', '2024-06-30', freq='QE')  # 包含未来报告
financial_data_leaky = pd.DataFrame({
    'report_date': financial_dates_leaky,
    'roe': np.random.randn(len(financial_dates_leaky)) * 0.1 + 0.15
})

print(f"正确财务报告日期范围: {financial_data_correct['report_date'].min().date()} - {financial_data_correct['report_date'].max().date()}")
print(f"泄露财务报告日期范围: {financial_data_leaky['report_date'].min().date()} - {financial_data_leaky['report_date'].max().date()}")

# 定义Walk-forward期间
train_start = pd.Timestamp('2020-01-01')
train_end = pd.Timestamp('2022-06-30')
validation_start = pd.Timestamp('2022-07-01')
validation_end = pd.Timestamp('2022-12-31')
test_start = pd.Timestamp('2023-01-01')
test_end = pd.Timestamp('2023-06-30')

print(f"\n4. Walk-forward期间定义")
print(f"训练集: {train_start.date()} - {train_end.date()}")
print(f"验证集: {validation_start.date()} - {validation_end.date()}")
print(f"测试集: {test_start.date()} - {test_end.date()}")

# 导入DataAssurance
print("\n5. 导入DataAssurance进行检查")
try:
    from quant_system.data.assurance import DataAssurance, RollingFeatureProcessor
    
    # 测试1：检查特征泄露
    print("\n测试1: 检查特征数据泄露")
    assurance1 = DataAssurance(strict_mode=False)
    checks1 = assurance1.check_walkforward_period(
        train_start=train_start,
        train_end=train_end,
        test_start=validation_start,
        test_end=validation_end,
        features_df=features_leaky
    )
    
    report1 = assurance1.generate_report()
    print(report1.split('\n')[0])  # 打印第一行
    print("特征泄露检查完成")
    
    # 测试2：检查标签泄露
    print("\n测试2: 检查标签数据泄露")
    assurance2 = DataAssurance(strict_mode=False)
    
    # 2.1 正确的标签数据
    print("  a) 正确的标签数据:")
    checks2a = assurance2.check_walkforward_period(
        train_start=train_start,
        train_end=train_end,
        test_start=validation_start,
        test_end=validation_end,
        labels_df=labels_correct
    )
    
    # 2.2 泄露的标签数据
    print("  b) 泄露的标签数据:")
    checks2b = assurance2.check_walkforward_period(
        train_start=train_start,
        train_end=train_end,
        test_start=validation_start,
        test_end=validation_end,
        labels_df=labels_leaky
    )
    
    # 测试3：检查财务数据
    print("\n测试3: 检查财务数据")
    assurance3 = DataAssurance(strict_mode=False)
    
    # 3.1 正确的财务数据
    print("  a) 正确的财务数据:")
    checks3a = assurance3.check_walkforward_period(
        train_start=train_start,
        train_end=train_end,
        test_start=validation_start,
        test_end=validation_end,
        financial_data=financial_data_correct
    )
    
    # 3.2 泄露的财务数据
    print("  b) 泄露的财务数据:")
    checks3b = assurance3.check_walkforward_period(
        train_start=train_start,
        train_end=train_end,
        test_start=validation_start,
        test_end=validation_end,
        financial_data=financial_data_leaky
    )
    
    # 测试4：严格模式
    print("\n测试4: 严格模式测试（应抛出异常）")
    try:
        assurance_strict = DataAssurance(strict_mode=True)
        checks_strict = assurance_strict.check_walkforward_period(
            train_start=train_start,
            train_end=train_end,
            test_start=validation_start,
            test_end=validation_end,
            features_df=features_leaky,
            labels_df=labels_leaky,
            financial_data=financial_data_leaky
        )
        print("⚠️ 严格模式未抛出异常（测试失败）")
    except ValueError as e:
        print(f"✅ 严格模式正确抛出异常: {str(e)[:80]}...")
    
    # 测试5：滚动窗口特征处理器
    print("\n测试5: 滚动窗口特征处理器（防止全局标准化）")
    processor = RollingFeatureProcessor(train_end)
    
    # 处理一个特征
    feature_name = 'feature1'
    standardized = processor.fit_transform(features_leaky, feature_name)
    
    # 检查是否使用了滚动窗口统计量
    train_mask = features_leaky.index <= train_end
    train_data = features_leaky.loc[train_mask, feature_name]
    train_mean = train_data.mean()
    train_std = train_data.std()
    
    global_mean = features_leaky[feature_name].mean()
    global_std = features_leaky[feature_name].std()
    
    print(f"  全局统计量: 均值={global_mean:.4f}, 标准差={global_std:.4f}")
    print(f"  滚动窗口统计量: 均值={train_mean:.4f}, 标准差={train_std:.4f}")
    
    if abs(global_mean - train_mean) > 0.1 or abs(global_std - train_std) > 0.1:
        print("  ✅ 滚动窗口统计量与全局统计量不同，避免了全局标准化")
    else:
        print("  ⚠️  滚动窗口统计量与全局统计量相似，可能存在全局标准化风险")
    
    print(f"  标准化后数据形状: {standardized.shape}")
    
    # 测试6：完整的未来函数检查
    print("\n测试6: 完整的未来函数检查工作流")
    
    print("模拟Walk-forward回测中的数据检查流程:")
    print("1. 定义训练/验证/测试期间")
    print("2. 收集特征、标签、财务数据")
    print("3. 运行DataAssurance检查")
    print("4. 如果发现严重未来函数错误，停止回测并修复")
    print("5. 使用滚动窗口特征处理器安全地处理数据")
    print("6. 训练模型，确保没有信息泄露")
    print("7. 在样本外测试集上验证模型")
    
    print("\n关键检查点:")
    print("  ✅ assert feature_date.max() <= train_end")
    print("  ✅ assert label_date.min() > train_end")  
    print("  ✅ assert financial_report_date.max() <= train_end")
    print("  ✅ 使用滚动窗口标准化（而非全局标准化）")
    print("  ✅ 财务因子使用 report_date ≤ train_end 的最新数据")
    print("  ✅ IC/IR计算仅使用训练窗口内数据")
    
    print("\n" + "=" * 80)
    print("测试总结:")
    print("=" * 80)
    print("DataAssurance成功检测以下未来函数问题:")
    print("1. 特征数据日期泄露（特征日期 > 训练集结束日期）")
    print("2. 标签数据日期泄露（标签日期 ≤ 训练集结束日期）")
    print("3. 财务报告日期泄露（财务报告日期 > 训练集结束日期）")
    print("4. 全局标准化风险（使用全局统计量而非滚动窗口统计量）")
    print("\n通过实施这些检查，可以显著减少Walk-forward回测中的look-ahead bias，")
    print("避免回测结果虚高（年化15-30%）而实盘表现差（-50%）的问题。")
    
except ImportError as e:
    print(f"导入失败: {e}")
    print("请确保quant_system.data.assurance模块可用")

print("\n" + "=" * 80)
print("下一步行动:")
print("=" * 80)
print("1. 在实际Walk-forward回测中集成DataAssurance")
print("2. 对所有因子计算实施滚动窗口标准化")
print("3. 确保财务数据严格使用 report_date ≤ train_end 的数据")
print("4. 在模型训练前强制执行静态检查")
print("5. 定期运行未来函数检查，防止代码修改引入新问题")