#!/usr/bin/env python3
"""
测试回填脚本中的Baostock集成
"""

import sys
import os
sys.path.append('/root/.openclaw/workspace/quant_system')

from data.database.backfill_all_stocks import HistoricalDataBackfiller

print("=" * 60)
print("测试回填脚本中的Baostock集成")
print("=" * 60)

# 创建回填器实例
backfiller = HistoricalDataBackfiller(max_workers=1)
print(f"数据源配置: {backfiller.data_sources}")

# 测试单个股票
test_symbols = ['600519', '000001', '300750']

for symbol in test_symbols:
    print(f"\n测试股票 {symbol}:")
    try:
        # 获取最近几天的数据（测试Baostock）
        df = backfiller.fetch_daily_prices(symbol, '2025-01-01', '2025-01-03')
        
        if df is not None and not df.empty:
            print(f"  ✅ 成功获取 {len(df)} 条数据")
            print(f"     数据范围: {df.index[0].strftime('%Y-%m-%d')} 至 {df.index[-1].strftime('%Y-%m-%d')}")
            print(f"     数据字段: {list(df.columns)}")
            
            # 检查是否包含Baostock特有字段
            baostock_fields = ['pe_ttm', 'pb_mrq', 'ps_ttm']
            has_baostock_fields = any(field in df.columns for field in baostock_fields)
            
            if has_baostock_fields:
                print(f"  ✅ 包含Baostock特有字段（基本面数据）")
                for field in baostock_fields:
                    if field in df.columns:
                        print(f"     {field}: {df.iloc[0][field]:.2f}")
            else:
                print(f"  ⚠️  不包含Baostock特有字段，可能来自其他数据源")
        else:
            print(f"  ❌ 获取数据失败或为空")
            
    except Exception as e:
        print(f"  ❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)