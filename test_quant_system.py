#!/usr/bin/env python3
import sys
sys.path.append('/root/.openclaw/workspace/quant_system')
try:
    from quant_main import QuantSystem
    print("导入原始QuantSystem成功")
except ImportError as e:
    print(f"导入失败: {e}")
    sys.exit(1)

# 初始化系统
quant = QuantSystem()
print("量化系统初始化成功")

# 测试股票评分
print("\n1. 测试股票评分...")
try:
    scores = quant.get_stock_scores(['000001', '000002'], days_back=30)
    print(f"评分结果: {scores}")
except Exception as e:
    print(f"评分失败: {e}")
    import traceback
    traceback.print_exc()

# 测试回测
print("\n2. 测试回测...")
try:
    backtest_result = quant.run_backtest(
        stock_codes=['000001'],
        start_date='2025-01-01',
        end_date='2025-03-20'
    )
    print(f"回测结果: {backtest_result}")
except Exception as e:
    print(f"回测失败: {e}")
    import traceback
    traceback.print_exc()

# 测试增强版系统
print("\n3. 测试增强版系统...")
try:
    from enhancements.enhanced_quant_system import EnhancedQuantSystem
    enhanced = EnhancedQuantSystem()
    print(f"增强版系统初始化成功，使用增强功能: {enhanced.use_enhanced_features}")
    
    # 快速测试
    if enhanced.use_enhanced_features:
        enhanced.run_quick_test()
except Exception as e:
    print(f"增强版测试失败: {e}")
    import traceback
    traceback.print_exc()