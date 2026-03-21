#!/usr/bin/env python3
"""
测试量化系统真实数据集成 - AKShare数据源
"""
import sys
import pandas as pd
sys.path.append('/root/.openclaw/workspace/quant_system')

print("=== 量化系统真实数据集成测试 ===")
print("测试目标: 验证量化系统能够使用AKShare真实数据工作")
print()

# 1. 测试数据管道
print("1. 测试数据管道...")
from data.sources.data_pipeline import DataPipeline
pipeline = DataPipeline()

# 测试AKShare数据源
print("  测试AKShare数据源...")
try:
    # 获取平安银行数据
    data = pipeline.get_stock_data('000001', start_date='2025-03-01', end_date='2025-03-20')
    
    if 'data' in data and not data['data'].empty:
        print(f"  ✅ 数据获取成功，形状: {data['data'].shape}")
        print(f"     数据源: {data['metadata']['source']['source_name']}")
        print(f"     数据质量: {data['metadata']['quality']['overall']:.2f}")
        print(f"     数据范围: {data['metadata']['source']['date_range']['start']} 至 {data['metadata']['source']['date_range']['end']}")
        
        # 显示前几行
        print(f"     前几行数据:")
        print(data['data'][['open', 'high', 'low', 'close', 'volume']].head(3))
    else:
        print("  ❌ 数据为空")
        
except Exception as e:
    print(f"  ❌ 数据获取失败: {e}")
    import traceback
    traceback.print_exc()

print()

# 2. 测试量化系统
print("2. 测试量化系统...")
try:
    from quant_main import QuantSystem
    quant = QuantSystem()
    print("  ✅ 量化系统初始化成功")
    
    # 测试股票评分
    print("  测试股票评分...")
    score_result = quant.get_stock_scores('000001', '2025-02-01', '2025-03-20')
    
    if 'error' in score_result:
        print(f"  ❌ 评分失败: {score_result['error']}")
    else:
        print(f"  ✅ 评分成功:")
        print(f"     股票: {score_result['symbol']}")
        print(f"     日期: {score_result['date']}")
        print(f"     价格: {score_result['price']:.2f}")
        print(f"     综合得分: {score_result['score']:.2f}/100")
        print(f"     评级: {score_result['score_category']}")
        print(f"     风险等级: {score_result['risk_level']}/5")
        print(f"     数据质量: {score_result['data_quality']:.2f}")
        print(f"     数据源: {score_result['data_source']}")
        
        if score_result['top_contributors']:
            print(f"     主要贡献因子:")
            for i, factor in enumerate(score_result['top_contributors'], 1):
                print(f"       {i}. {factor['name']}: {factor['contribution']:.2f}分 ({factor['contribution_pct']:.1f}%)")
    
    print()
    
    # 测试回测
    print("  测试回测...")
    backtest_result = quant.run_backtest(
        symbols=['000001'], 
        start_date='2025-01-01',
        end_date='2025-03-20'
    )
    
    print(f"  ✅ 回测完成:")
    print(f"     状态: {backtest_result['status']}")
    print(f"     收益率: {backtest_result.get('total_return', 0):.2%}")
    print(f"     交易次数: {backtest_result.get('total_trades', 0)}")
    
    if 'performance_summary' in backtest_result:
        perf = backtest_result['performance_summary']
        print(f"     夏普比率: {perf.get('sharpe_ratio', 0):.2f}")
        print(f"     最大回撤: {perf.get('max_drawdown', 0):.2%}")
    
except Exception as e:
    print(f"  ❌ 量化系统测试失败: {e}")
    import traceback
    traceback.print_exc()

print()

# 3. 测试增强版系统
print("3. 测试增强版系统...")
try:
    from enhancements.enhanced_quant_system import EnhancedQuantSystem
    enhanced = EnhancedQuantSystem()
    
    if enhanced.use_enhanced_features:
        print(f"  ✅ 增强版系统初始化成功")
        print(f"     增强模块: {len(enhanced.enhanced_modules)}个")
        
        # 快速测试
        print("  运行增强模块快速测试...")
        enhanced.run_quick_test()
    else:
        print("  ⚠️ 增强功能不可用，使用基础功能")
        
except Exception as e:
    print(f"  ❌ 增强版系统测试失败: {e}")
    import traceback
    traceback.print_exc()

print()
print("=== 测试完成 ===")