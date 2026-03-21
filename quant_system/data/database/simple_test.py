#!/usr/bin/env python3
"""
简化测试脚本 - 验证数据库核心功能
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime

# 添加路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_basic_operations():
    """测试数据库基本操作"""
    from database.database_manager import DatabaseManager
    
    print("=" * 60)
    print("测试数据库基本操作")
    print("=" * 60)
    
    # 创建数据库管理器
    db = DatabaseManager()
    
    # 测试1: 插入股票信息
    print("\n1. 测试股票信息操作...")
    test_symbol = '000001'
    
    success = db.upsert_stock(
        symbol=test_symbol,
        name='平安银行',
        market='SZ',
        listing_date='1991-04-03',
        industry='银行',
        sub_industry='股份制银行',
        status='active'
    )
    
    print(f"   插入股票信息: {'成功' if success else '失败'}")
    
    # 读取股票信息
    stock_info = db.get_stock_info(test_symbol)
    if stock_info:
        print(f"   读取股票信息: {stock_info['name']} ({stock_info['symbol']})")
    else:
        print(f"   读取股票信息失败")
    
    # 测试2: 插入价格数据
    print("\n2. 测试价格数据操作...")
    
    # 创建模拟价格数据
    dates = pd.date_range(start='2025-01-01', end='2025-01-03', freq='D')
    
    test_prices = pd.DataFrame({
        'open': [10.0, 10.2, 10.1],
        'high': [10.5, 10.6, 10.4],
        'low': [9.8, 10.0, 9.9],
        'close': [10.3, 10.4, 10.2],
        'volume': [1000000, 1200000, 1100000],
        'amount': [1.03e7, 1.25e7, 1.12e7],
        'change': [0.3, 0.1, -0.2],
        'change_pct': [3.0, 1.0, -1.9],
        'turnover': [0.5, 0.6, 0.55],
        'amplitude': [7.0, 5.9, 5.0]
    }, index=dates)
    
    # 插入价格数据
    new_count, update_count = db.insert_daily_prices(test_symbol, test_prices)
    print(f"   插入价格数据: 新增{new_count}条, 更新{update_count}条")
    
    # 查询价格数据
    prices = db.get_daily_prices(test_symbol, '2025-01-01', '2025-01-03')
    print(f"   查询到{len(prices)}条价格记录")
    
    if not prices.empty:
        print(f"   最新收盘价: {prices['close'].iloc[-1]:.2f}元")
        print(f"   价格范围: {prices['low'].min():.2f} - {prices['high'].max():.2f}元")
    
    # 测试3: 数据质量检查
    print("\n3. 测试数据质量检查...")
    quality = db.check_data_quality(test_symbol)
    print(f"   数据质量评分: {quality.get(test_symbol, {}).get('avg_quality', 0):.3f}")
    
    # 测试4: 数据库统计
    print("\n4. 测试数据库统计...")
    stats = db.get_update_stats()
    print(f"   数据库大小: {stats['database_size_mb']:.2f} MB")
    print(f"   日线记录数: {stats['total_daily_records']:,}条")
    
    return True

def test_backfill_simple():
    """测试简单回填"""
    print("\n" + "=" * 60)
    print("测试简单回填功能")
    print("=" * 60)
    
    from database.backfill_all_stocks import HistoricalDataBackfiller
    
    # 创建回填器（单线程避免并发问题）
    backfiller = HistoricalDataBackfiller(max_workers=1)
    
    # 只测试1支股票
    test_symbols = ['000002']  # 万科A
    
    print(f"测试回填 {len(test_symbols)} 支股票:")
    for symbol in test_symbols:
        print(f"  - {symbol}")
    
    # 处理单只股票
    result = backfiller.process_single_stock(test_symbols[0], force_update=True)
    
    print(f"\n回填结果: {'成功' if result.get('success', False) else '失败'}")
    
    if result.get('success', False):
        steps = result.get('steps', {})
        print(f"处理步骤:")
        
        for step_name, step_info in steps.items():
            success = step_info.get('success', False)
            print(f"  {step_name}: {'✅' if success else '❌'}")
            
            if step_name == 'price' and success:
                new = step_info.get('new', 0)
                updated = step_info.get('updated', 0)
                print(f"    新增{new}条, 更新{updated}条")
    
    return result.get('success', False)

def main():
    """主测试函数"""
    print("量化历史数据库简化测试")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    import time
    start_time = time.time()
    
    try:
        # 测试基本操作
        test_basic_operations()
        
        # 测试回填功能（可能需要网络）
        print("\n" + "=" * 60)
        print("开始回填功能测试...")
        print("注意：这需要网络连接获取数据")
        print("=" * 60)
        
        backfill_success = test_backfill_simple()
        
    except Exception as e:
        print(f"\n测试过程中出现异常: {e}")
        import traceback
        traceback.print_exc()
        backfill_success = False
    
    elapsed = time.time() - start_time
    
    # 最终报告
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
    print(f"总耗时: {elapsed:.1f}秒")
    print(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 检查数据库文件
    db_path = "/root/.openclaw/workspace/quant_system/data/database/quant_history.db"
    if os.path.exists(db_path):
        size_mb = os.path.getsize(db_path) / (1024 * 1024)
        print(f"数据库文件: {db_path}")
        print(f"文件大小: {size_mb:.2f} MB")
    
    print("\n✅ 简化测试完成!")
    print("\n下一步:")
    print("1. 运行完整回填: python3 backfill_all_stocks.py --test")
    print("2. 测试每日更新: python3 daily_update.py --test")
    print("3. 更新DataPipeline使用本地数据库")

if __name__ == "__main__":
    main()