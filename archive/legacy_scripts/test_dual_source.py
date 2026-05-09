#!/usr/bin/env python3
"""
测试双数据源集成
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "skills/quant/lib"))

# 使用虚拟环境
venv_python = "/root/.openclaw/workspace/quant_venv/bin/python"
if not os.path.exists(venv_python):
    print("虚拟环境未找到，使用系统Python")
else:
    # 重新使用虚拟环境执行
    print("使用虚拟环境执行")
    os.execv(venv_python, [venv_python] + sys.argv)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import traceback

def test_datasource_module():
    """测试数据源模块"""
    print("=== 测试数据源模块 ===")
    
    try:
        from datasource import DataSourceManager, BaostockAdapter, AKShareAdapter
        
        print("1. 测试 BaostockAdapter...")
        bs = BaostockAdapter()
        connected = bs.test_connection()
        print(f"   Baostock 连接测试: {'成功' if connected else '失败'}")
        
        print("2. 测试 AKShareAdapter...")
        ak = AKShareAdapter()
        connected = ak.test_connection()
        print(f"   AKShare 连接测试: {'成功' if connected else '失败'}")
        
        print("3. 测试 DataSourceManager...")
        manager = DataSourceManager({
            'priority': ['baostock', 'akshare'],
            'cache_enabled': True,
            'cache_dir': '/tmp/quant_test_cache'
        })
        
        status = manager.get_status()
        print(f"   数据源状态: Baostock={status['baostock']['available']}, "
              f"AKShare={status['akshare']['available']}")
        
        # 测试数据获取
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        
        print(f"4. 获取测试数据 ({start_date} 到 {end_date})...")
        symbols = ['600519.SH', '000001.SZ']
        
        for symbol in symbols:
            try:
                print(f"   获取 {symbol}...")
                df = manager.get_daily(symbol, start_date, end_date, 'qfq')
                if df is not None and len(df) > 0:
                    print(f"     成功: {len(df)} 行数据")
                    print(f"     列: {list(df.columns)}")
                    print(f"     日期范围: {df.index.min()} 到 {df.index.max()}")
                else:
                    print(f"     失败: 无数据")
            except Exception as e:
                print(f"     异常: {e}")
        
        # 测试缓存
        print("5. 测试缓存功能...")
        cache_key = manager._get_cache_key('600519.SH', start_date, end_date, 'qfq')
        print(f"   缓存键: {cache_key}")
        
        # 清理
        manager.cleanup()
        print("6. 测试完成，资源已清理")
        
        return True
        
    except Exception as e:
        print(f"数据源模块测试失败: {e}")
        traceback.print_exc()
        return False

def test_data_py_integration():
    """测试 data.py 集成"""
    print("\n=== 测试 data.py 集成 ===")
    
    try:
        # 导入 data 模块
        import data
        
        print("1. 测试 get_stock 函数...")
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        
        df = data.get_stock('600519.SH', start_date, end_date, 'qfq')
        
        if df is not None and len(df) > 0:
            print(f"   成功获取数据: {len(df)} 行")
            print(f"   列: {list(df.columns)}")
            print(f"   前2行数据:")
            print(df.head(2))
        else:
            print("   获取数据失败")
        
        print("2. 测试数据源状态...")
        status = data.get_data_source_status()
        if status:
            print(f"   数据源状态: {status}")
        else:
            print("   无数据源状态信息")
        
        print("3. 测试清理...")
        data.cleanup()
        print("   清理完成")
        
        return True
        
    except Exception as e:
        print(f"data.py 集成测试失败: {e}")
        traceback.print_exc()
        return False

def test_compare_sources():
    """对比双数据源数据一致性"""
    print("\n=== 对比数据源一致性 ===")
    
    try:
        from datasource import DataSourceManager
        
        manager = DataSourceManager({
            'priority': ['baostock', 'akshare'],
            'cache_enabled': False
        })
        
        # 确保两个数据源都可用
        status = manager.get_status()
        if not status['baostock']['available'] or not status['akshare']['available']:
            print("   需要两个数据源都可用，跳过对比测试")
            return False
        
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        
        print(f"   获取 Baostock 数据...")
        bs_df = manager.baostock.get_daily('600519.SH', start_date, end_date, 'qfq')
        
        print(f"   获取 AKShare 数据...")
        ak_df = manager.akshare.get_daily('600519.SH', start_date, end_date, 'qfq')
        
        if bs_df is not None and ak_df is not None:
            result = manager.validate_data(bs_df, ak_df, threshold=1.0)
            print(f"   数据一致性验证结果:")
            for key, value in result.items():
                print(f"     {key}: {value}")
            
            if result['valid']:
                print("   ✅ 数据一致性良好")
            else:
                print("   ⚠️ 数据差异较大，需注意")
        
        manager.cleanup()
        return True
        
    except Exception as e:
        print(f"数据对比测试失败: {e}")
        traceback.print_exc()
        return False

def test_performance():
    """测试性能"""
    print("\n=== 测试性能 ===")
    
    try:
        from datasource import DataSourceManager
        import time
        
        manager = DataSourceManager({
            'priority': ['baostock', 'akshare'],
            'cache_enabled': True
        })
        
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
        
        symbols = ['600519.SH', '000001.SZ', '000858.SZ']
        
        total_time = 0
        success_count = 0
        
        for symbol in symbols:
            try:
                start_time = time.time()
                df = manager.get_daily(symbol, start_date, end_date, 'qfq')
                elapsed = time.time() - start_time
                
                if df is not None and len(df) > 0:
                    success_count += 1
                    total_time += elapsed
                    print(f"   {symbol}: {len(df)} 行，耗时 {elapsed:.2f} 秒")
                else:
                    print(f"   {symbol}: 失败")
                    
            except Exception as e:
                print(f"   {symbol}: 异常 - {e}")
        
        if success_count > 0:
            avg_time = total_time / success_count
            print(f"   平均获取时间: {avg_time:.2f} 秒/股票")
        
        manager.cleanup()
        return True
        
    except Exception as e:
        print(f"性能测试失败: {e}")
        traceback.print_exc()
        return False

def main():
    print("开始双数据源集成测试")
    print(f"Python 路径: {sys.executable}")
    print(f"当前时间: {datetime.now()}")
    
    # 运行测试
    tests = [
        ("数据源模块", test_datasource_module),
        ("data.py 集成", test_data_py_integration),
        ("数据一致性", test_compare_sources),
        ("性能测试", test_performance)
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n{'='*60}")
        print(f"测试: {test_name}")
        print('='*60)
        
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"测试异常: {e}")
            results.append((test_name, False))
    
    # 汇总结果
    print(f"\n{'='*60}")
    print("测试结果汇总")
    print('='*60)
    
    all_passed = True
    for test_name, success in results:
        status = "✅ 通过" if success else "❌ 失败"
        print(f"{test_name}: {status}")
        if not success:
            all_passed = False
    
    print('='*60)
    if all_passed:
        print("🎉 所有测试通过！双数据源方案准备就绪。")
    else:
        print("⚠️ 部分测试失败，需要进一步调试。")
    
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)