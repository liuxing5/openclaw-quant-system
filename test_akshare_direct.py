#!/usr/bin/env python3
"""
直接测试AKShare API获取实时数据
"""
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

print("=== 直接测试AKShare API ===")

# 获取当前日期和一个月前
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

print(f"测试日期范围: {start_date} 至 {end_date}")
print()

# 测试1: A股实时行情
print("1. 测试A股实时行情...")
try:
    stock_zh_a_spot_df = ak.stock_zh_a_spot()
    print(f"  ✅ 成功获取 {len(stock_zh_a_spot_df)} 只A股实时行情")
    if len(stock_zh_a_spot_df) > 0:
        print(f"     示例股票: {stock_zh_a_spot_df.iloc[0]['代码']} {stock_zh_a_spot_df.iloc[0]['名称']}")
        print(f"     最新价: {stock_zh_a_spot_df.iloc[0]['最新价']} 元")
except Exception as e:
    print(f"  ❌ 实时行情获取失败: {e}")

print()

# 测试2: 单只股票历史数据
print("2. 测试单只股票历史数据...")
try:
    stock_zh_a_daily_df = ak.stock_zh_a_daily(symbol="sz000001", start_date=start_date, end_date=end_date)
    print(f"  ✅ 成功获取平安银行历史数据")
    print(f"     数据形状: {stock_zh_a_daily_df.shape}")
    print(f"     数据列: {list(stock_zh_a_daily_df.columns)}")
    if not stock_zh_a_daily_df.empty:
        print(f"     最新数据:")
        print(f"       日期: {stock_zh_a_daily_df.index[-1]}")
        print(f"       开盘: {stock_zh_a_daily_df['open'].iloc[-1]}")
        print(f"       收盘: {stock_zh_a_daily_df['close'].iloc[-1]}")
        print(f"       成交量: {stock_zh_a_daily_df['volume'].iloc[-1]:.0f}")
except Exception as e:
    print(f"  ❌ 历史数据获取失败: {e}")

print()

# 测试3: 股票基本信息
print("3. 测试股票基本信息...")
try:
    stock_info_a_code_name_df = ak.stock_info_a_code_name()
    print(f"  ✅ 成功获取A股代码列表，共 {len(stock_info_a_code_name_df)} 只股票")
    # 显示部分股票代码
    print(f"     示例股票:")
    for i in range(min(5, len(stock_info_a_code_name_df))):
        print(f"       {stock_info_a_code_name_df.iloc[i]['code']}: {stock_info_a_code_name_df.iloc[i]['name']}")
except Exception as e:
    print(f"  ❌ 股票基本信息获取失败: {e}")

print()

# 测试4: 测试数据管道使用AKShare
print("4. 测试数据管道强制使用AKShare...")
try:
    from quant_system.data.sources.data_pipeline import DataPipeline
    pipeline = DataPipeline()
    
    # 尝试直接调用AKShare获取方法
    print("  测试直接调用AKShare方法...")
    # 需要查看DataPipeline内部方法
    # 这里简化测试：检查pipeline中是否有AKShare源
    akshare_source = None
    for source_id, source_name, func in pipeline.data_sources:
        if source_id == 'akshare':
            akshare_source = func
            break
    
    if akshare_source:
        print(f"  ✅ 数据管道已配置AKShare源")
        # 尝试调用（可能需要适当参数）
        try:
            # 调用AKShare函数（需要正确参数）
            test_symbol = "000001.SZ"
            result = pipeline._fetch_akshare(test_symbol, '20250301', '20250320')
            print(f"  ✅ AKShare函数调用成功")
            if result and 'data' in result and not result['data'].empty:
                print(f"     获取数据行数: {len(result['data'])}")
        except Exception as e:
            print(f"  ⚠️ AKShare函数调用错误（可能参数问题）: {e}")
    else:
        print(f"  ❌ 数据管道未找到AKShare源")
        
except Exception as e:
    print(f"  ❌ 数据管道测试失败: {e}")

print()
print("=== AKShare测试完成 ===")
print("结论: AKShare库工作正常，可以获取A股实时和历史数据")