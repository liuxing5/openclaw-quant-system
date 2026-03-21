#!/usr/bin/env python3
"""
快速验证语法修复
"""

import sys
import os

def test_import_advanced_backtester():
    """测试高级回测器导入"""
    try:
        from advanced_backtest.advanced_backtester import AdvancedBacktester
        print("✅ 高级回测器导入成功")
        return True
    except Exception as e:
        print(f"❌ 高级回测器导入失败: {e}")
        return False

def test_import_advanced_risk():
    """测试高级风险管理系统导入"""
    try:
        from advanced_risk.advanced_risk_manager import AdvancedRiskManager
        print("✅ 高级风险管理系统导入成功")
        return True
    except Exception as e:
        print(f"❌ 高级风险管理系统导入失败: {e}")
        return False

def test_import_refined_sentiment():
    """测试精细化情绪因子导入"""
    try:
        from advanced_sentiment.refined_sentiment import RefinedSentimentFactor
        print("✅ 精细化情绪因子导入成功")
        return True
    except Exception as e:
        print(f"❌ 精细化情绪因子导入失败: {e}")
        return False

def main():
    print("=" * 60)
    print("快速验证语法修复")
    print("=" * 60)
    
    results = []
    
    results.append(("高级回测器", test_import_advanced_backtester()))
    results.append(("高级风险管理系统", test_import_advanced_risk()))
    results.append(("精细化情绪因子", test_import_refined_sentiment()))
    
    print("\n" + "=" * 60)
    print("验证结果:")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅" if passed else "❌"
        print(f"{status} {name}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\n✅ 所有模块语法验证通过！")
        return True
    else:
        print("\n❌ 部分模块存在语法问题")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)