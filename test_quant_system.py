#!/usr/bin/env python3
"""
测试QuantSystem功能
"""

import sys
sys.path.append('/root/.openclaw/workspace/quant_system')

try:
    import quant_main
    
    # 创建QuantSystem实例
    qs = quant_main.QuantSystem()
    print("✅ QuantSystem实例创建成功")
    
    # 检查PIT方法
    methods = ['enable_pit_mode', 'disable_pit_mode', 'set_pit_current_date', 'get_pit_violations', 'get_pit_stats']
    
    for method in methods:
        if hasattr(qs, method):
            print(f"✅ QuantSystem有方法: {method}")
        else:
            print(f"❌ QuantSystem缺少方法: {method}")
            
    # 测试归因分析模块导入
    try:
        from attribution.brinson_attribution import BrinsonAttribution
        print("✅ Brinson归因分析模块可导入")
    except ImportError as e:
        print(f"❌ Brinson归因分析模块导入失败: {e}")
        
    # 测试PIT模块导入
    try:
        from pit_factors.pit_factor_manager import PITFactorManager
        print("✅ PIT因子管理器模块可导入")
    except ImportError as e:
        print(f"❌ PIT因子管理器模块导入失败: {e}")
        
except Exception as e:
    print(f"❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()