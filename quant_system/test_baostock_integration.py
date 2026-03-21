#!/usr/bin/env python3
"""
测试Baostock集成
"""

import sys
import os
sys.path.append('/root/.openclaw/workspace/quant_system')

print("=" * 60)
print("测试Baostock集成到数据管道")
print("=" * 60)

# 测试1: 检查Baostock模块
print("\n1. 检查Baostock模块...")
try:
    import baostock as bs
    print("  ✅ Baostock模块可用")
    
    # 测试登录
    lg = bs.login()
    print(f"  登录状态: {lg.error_msg}")
    
    if lg.error_code == '0':
        print("  ✅ Baostock登录成功")
        
        # 测试简单查询
        rs = bs.query_history_k_data_plus(
            "sh.600519",
            "date,code,open,high,low,close",
            start_date="2025-01-01",
            end_date="2025-01-03",
            frequency="d",
            adjustflag="3"
        )
        
        if rs.error_code == '0':
            df = rs.get_data()
            print(f"  ✅ 查询成功，获取 {len(df)} 条数据")
            if not df.empty:
                print(f"    示例数据:")
                print(f"      日期: {df.iloc[0]['date']}")
                print(f"      开盘: {df.iloc[0]['open']}")
                print(f"      收盘: {df.iloc[0]['close']}")
        else:
            print(f"  ❌ 查询失败: {rs.error_msg}")
        
        bs.logout()
        print("  ✅ Baostock登出成功")
    else:
        print(f"  ❌ Baostock登录失败: {lg.error_msg}")
        
except Exception as e:
    print(f"  ❌ Baostock测试失败: {e}")
    import traceback
    traceback.print_exc()

# 测试2: 测试数据管道
print("\n2. 测试数据管道集成...")
try:
    from data.sources.data_pipeline import DataPipeline
    
    pipeline = DataPipeline()
    print(f"  ✅ 数据管道初始化成功")
    print(f"     可用数据源: {[name for _, name, _ in pipeline.data_sources]}")
    
    # 检查Baostock是否在数据源中
    baostock_included = any(source_id == 'baostock' for source_id, _, _ in pipeline.data_sources)
    if baostock_included:
        print(f"  ✅ Baostock已包含在数据源中（优先级2）")
    else:
        print(f"  ❌ Baostock未包含在数据源中")
        
except Exception as e:
    print(f"  ❌ 数据管道测试失败: {e}")
    import traceback
    traceback.print_exc()

# 测试3: 测试股票代码格式转换
print("\n3. 测试股票代码格式转换...")
try:
    from data.sources.data_pipeline import DataPipeline
    pipeline = DataPipeline()
    
    test_cases = [
        ('600519', 'sh.600519'),
        ('000001', 'sz.000001'),
        ('300750', 'sz.300750'),
        ('600519.SH', 'sh.600519'),
        ('000001.SZ', 'sz.000001'),
    ]
    
    for input_symbol, expected in test_cases:
        result = pipeline._format_symbol_for_baostock(input_symbol)
        status = "✅" if result == expected else "❌"
        print(f"  {status} {input_symbol} -> {result} (期望: {expected})")
        
except Exception as e:
    print(f"  ❌ 格式转换测试失败: {e}")

# 测试4: 实际数据获取测试
print("\n4. 实际数据获取测试...")
try:
    pipeline = DataPipeline()
    
    # 先确保数据库中没有这个数据
    if pipeline.db:
        with pipeline.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM daily_prices WHERE symbol = 'TEST600519'")
            conn.commit()
    
    # 测试获取数据（应该从Baostock获取）
    print("  测试从Baostock获取贵州茅台数据...")
    result = pipeline.get_stock_data('600519', '2025-01-01', '2025-01-03', with_metadata=True)
    
    if 'data' in result and not result['data'].empty:
        df = result['data']
        metadata = result['metadata']
        
        print(f"  ✅ 成功获取 {len(df)} 条数据")
        print(f"     数据源: {metadata['source']['source_name']}")
        print(f"     数据质量: {metadata['quality']['overall']:.3f}")
        
        if metadata['source']['source_id'] == 'baostock':
            print(f"  ✅ 成功从Baostock获取数据（核心数据源生效）")
        else:
            print(f"  ⚠️  从{metadata['source']['source_name']}获取数据")
            
        # 显示数据详情
        print(f"     数据字段: {list(df.columns)}")
        for i in range(min(3, len(df))):
            date_str = df.index[i].strftime('%Y-%m-%d')
            print(f"       {date_str}: 开{df.iloc[i]['open']:.2f} 收{df.iloc[i]['close']:.2f} 量{df.iloc[i]['volume']:.0f}")
    else:
        print(f"  ❌ 获取数据失败或为空")
        
except Exception as e:
    print(f"  ❌ 实际数据获取测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("Baostock集成测试完成")
print("=" * 60)

print("\n下一步:")
print("1. 运行批量回填测试: python3 data/database/backfill_all_stocks.py --test")
print("2. 验证基本面数据获取")
print("3. 测试AKShare补充功能")