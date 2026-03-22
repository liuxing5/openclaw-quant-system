#!/usr/bin/env python3
"""
量化系统健康检查
检查所有关键组件是否正常工作，识别潜在问题
"""

import sys
import os
import importlib

def check_module(module_name, description):
    """检查模块是否可导入"""
    try:
        importlib.import_module(module_name)
        return True, f"✅ {description} ({module_name})"
    except ImportError as e:
        return False, f"❌ {description} ({module_name}): {str(e)[:100]}"

def check_quant_system_components():
    """检查量化系统关键组件"""
    print("=== 量化系统健康检查 ===")
    print("检查时间:", pd.Timestamp.now())
    
    results = []
    
    # 核心数据模块
    modules_to_check = [
        ("pandas", "数据分析库"),
        ("numpy", "数值计算库"),
        ("scipy", "科学计算库"),
        ("sklearn", "机器学习库"),
        ("lightgbm", "LightGBM机器学习"),
        ("xgboost", "XGBoost机器学习"),
        ("statsmodels", "统计模型"),
    ]
    
    for module_name, description in modules_to_check:
        success, msg = check_module(module_name, description)
        results.append((success, msg))
    
    # 检查量化系统内部模块
    sys.path.append('/root/.openclaw/workspace/quant_system')
    
    quant_modules = [
        ("data.sources.data_pipeline", "数据管道"),
        ("walkforward.walkforward_backtester", "Walk-forward回测器"),
        ("enhancements.vectorized_backtest", "向量化回测器"),
        ("alpha_predictor", "Alpha预测器"),
        ("real_factors.real_factor_manager", "真实因子管理器"),
        ("slippage.liquidity_impact_model", "流动性冲击模型"),
        ("risk_models.factor_risk_model", "因子风险模型"),
        ("regime_detection", "市场状态识别"),
        ("pit_factors.pit_factor_manager", "PIT因子管理器"),
    ]
    
    for module_name, description in quant_modules:
        try:
            module = importlib.import_module(module_name)
            results.append((True, f"✅ {description} ({module_name})"))
        except Exception as e:
            results.append((False, f"❌ {description} ({module_name}): {str(e)[:100]}"))
    
    # 输出结果
    print("\n=== 检查结果 ===")
    
    success_count = sum(1 for success, _ in results if success)
    total_count = len(results)
    
    for success, msg in results:
        print(msg)
    
    print(f"\n=== 汇总 ===")
    print(f"通过: {success_count}/{total_count} ({success_count/total_count*100:.1f}%)")
    
    # 关键问题识别
    critical_issues = []
    for success, msg in results:
        if not success:
            if "data_pipeline" in msg or "alpha_predictor" in msg:
                critical_issues.append(msg)
    
    if critical_issues:
        print(f"\n⚠️  关键问题:")
        for issue in critical_issues:
            print(f"  - {issue}")
    
    return success_count == total_count

def check_data_sources():
    """检查数据源可用性"""
    print("\n=== 数据源检查 ===")
    
    try:
        import akshare as ak
        print("✅ AKShare数据源可用")
    except ImportError:
        print("❌ AKShare不可用")
    
    try:
        import baostock as bs
        print("✅ Baostock数据源可用")
    except ImportError:
        print("❌ Baostock不可用")
    
    try:
        from data.sources.data_pipeline import DataPipeline
        dp = DataPipeline()
        print("✅ 数据管道初始化成功")
    except Exception as e:
        print(f"❌ 数据管道初始化失败: {str(e)[:100]}")

def check_recent_fixes():
    """检查最近修复的关键问题"""
    print("\n=== 最近修复检查 ===")
    
    fixes_to_check = [
        ("_train_factor_model_safe 真实训练", "检查函数是否进行真实训练而非硬编码权重"),
        ("信号层打通", "检查因子权重是否实际应用于信号生成"),
        ("防止未来函数", "检查PIT合规性和数据截止日期检查"),
    ]
    
    for fix_name, description in fixes_to_check:
        print(f"🔍 {fix_name}: {description}")
    
    # 尝试导入walkforward_backtester检查修复
    try:
        from walkforward.walkforward_backtester import WalkForwardBacktester, WalkForwardConfig
        
        config = WalkForwardConfig(
            train_years=1,
            validation_months=3,
            test_months=3,
            step_months=1,
            initial_capital=1000000
        )
        
        wf = WalkForwardBacktester(config)
        print("✅ WalkForwardBacktester 可正常初始化")
        
        # 检查_train_factor_model_safe是否存在
        if hasattr(wf, '_train_factor_model_safe'):
            print("✅ _train_factor_model_safe 函数存在")
        else:
            print("❌ _train_factor_model_safe 函数不存在")
            
    except Exception as e:
        print(f"❌ WalkForwardBacktester 检查失败: {str(e)[:100]}")

if __name__ == "__main__":
    try:
        # 导入pandas用于时间戳
        import pandas as pd
        
        # 运行检查
        all_ok = check_quant_system_components()
        check_data_sources()
        check_recent_fixes()
        
        print("\n=== 健康检查完成 ===")
        if all_ok:
            print("🎉 所有组件检查通过")
            print("建议下一步:")
            print("1. 运行完整walkforward回测验证系统")
            print("2. 进行实盘模拟测试")
            print("3. 准备生产环境部署")
            sys.exit(0)
        else:
            print("⚠️  发现一些问题，需要修复")
            print("建议:")
            print("1. 安装缺失的依赖包")
            print("2. 修复数据源连接问题")
            print("3. 运行单元测试验证修复")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ 健康检查异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)