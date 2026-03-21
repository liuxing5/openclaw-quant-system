#!/usr/bin/env python3
"""
测试回填脚本 - 快速验证数据库功能
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database_manager import DatabaseManager
from database.backfill_all_stocks import HistoricalDataBackfiller

def test_database_operations():
    """测试数据库基本操作"""
    print("=" * 60)
    print("测试数据库基本操作")
    print("=" * 60)
    
    db = DatabaseManager()
    
    # 测试1: 股票信息操作
    print("\n1. 测试股票信息操作...")
    test_symbol = '600519'
    
    # 插入股票信息
    db.upsert_stock(
        symbol=test_symbol,
        name='贵州茅台(测试)',
        market='SH',
        listing_date='2001-08-27',
        industry='食品饮料',
        sub_industry='白酒'
    )
    
    # 读取股票信息
    stock_info = db.get_stock_info(test_symbol)
    print(f"   股票信息: {stock_info}")
    
    # 测试2: 价格数据操作
    print("\n2. 测试价格数据操作...")
    
    # 模拟价格数据
    import pandas as pd
    import numpy as np
    dates = pd.date_range(start='2025-01-01', end='2025-01-05', freq='D')
    
    test_prices = pd.DataFrame({
        'open': [1600, 1610, 1595, 1620, 1615],
        'high': [1620, 1625, 1605, 1630, 1625],
        'low': [1590, 1605, 1585, 1610, 1605],
        'close': [1610, 1615, 1600, 1625, 1620],
        'volume': [1000000, 1200000, 950000, 1100000, 1050000],
        'amount': [1.61e9, 1.94e9, 1.52e9, 1.79e9, 1.70e9],
        'change': [10, 5, -15, 25, -5],
        'change_pct': [0.62, 0.31, -0.93, 1.56, -0.31],
        'turnover': [0.5, 0.6, 0.48, 0.55, 0.53],
        'amplitude': [1.88, 1.24, 1.25, 1.24, 1.24]
    }, index=dates)
    
    new_count, update_count = db.insert_daily_prices(test_symbol, test_prices)
    print(f"   插入价格数据: 新增{new_count}条, 更新{update_count}条")
    
    # 查询价格数据
    prices = db.get_daily_prices(test_symbol, '2025-01-01', '2025-01-05')
    print(f"   查询到{len(prices)}条价格记录")
    
    if not prices.empty:
        print(f"   最新价格: {prices['close'].iloc[-1]:.2f}元")
    
    # 测试3: 数据质量检查
    print("\n3. 测试数据质量检查...")
    quality = db.check_data_quality(test_symbol)
    print(f"   数据质量: {quality}")
    
    # 测试4: 更新统计
    print("\n4. 测试更新统计...")
    stats = db.get_update_stats()
    print(f"   数据库统计: {stats}")
    
    print("\n✅ 数据库基本操作测试完成!")

def test_backfill_small():
    """测试小规模回填"""
    print("\n" + "=" * 60)
    print("测试小规模回填")
    print("=" * 60)
    
    backfiller = HistoricalDataBackfiller(max_workers=2)
    
    # 只测试3支股票
    test_symbols = ['600519', '300750', '002415']
    
    print(f"测试回填 {len(test_symbols)} 支股票:")
    for symbol in test_symbols:
        print(f"  - {symbol}")
    
    # 运行回填
    results = backfiller.process_batch(test_symbols, force_update=True)
    
    # 统计结果
    success_count = sum(1 for r in results if r.get('success', False))
    print(f"\n回填结果: {success_count}成功, {len(results)-success_count}失败")
    
    # 显示详细结果
    for result in results:
        symbol = result['symbol']
        success = result.get('success', False)
        steps = result.get('steps', {})
        
        print(f"\n{symbol}: {'✅' if success else '❌'}")
        
        if 'price' in steps:
            price_step = steps['price']
            if price_step.get('success', False):
                new = price_step.get('new', 0)
                updated = price_step.get('updated', 0)
                print(f"  价格数据: {new}新增, {updated}更新")
        
        if not success and 'error' in result:
            print(f"  错误: {result['error']}")
    
    return success_count == len(test_symbols)

def test_database_query():
    """测试数据库查询功能"""
    print("\n" + "=" * 60)
    print("测试数据库查询功能")
    print("=" * 60)
    
    db = DatabaseManager()
    
    # 测试价格矩阵查询
    symbols = ['600519', '300750', '002415']
    
    print("测试多股票价格矩阵查询...")
    price_matrix = db.get_price_matrix(symbols, '2025-01-01', '2025-01-05', 'close')
    
    if not price_matrix.empty:
        print(f"   获取到{len(price_matrix)}行, {len(price_matrix.columns)}列数据")
        print(f"   日期范围: {price_matrix.index[0]} 至 {price_matrix.index[-1]}")
        
        # 显示前几行
        print("\n   前3行数据:")
        print(price_matrix.head(3))
    else:
        print("   无数据返回")
    
    # 测试收益率计算
    print("\n测试收益率计算...")
    for symbol in symbols[:2]:
        returns = db.get_returns(symbol, '2025-01-01', '2025-01-05')
        if not returns.empty:
            print(f"   {symbol}: {len(returns)}个收益率数据")
            print(f"     平均日收益: {returns.mean()*100:.2f}%")
            print(f"     波动率: {returns.std()*100:.2f}%")
    
    print("\n✅ 数据库查询功能测试完成!")

def main():
    """主测试函数"""
    import datetime
    print("量化历史数据库测试脚本")
    print(f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 运行测试
    test_database_operations()
    
    # 小规模回填测试（可能需要一些时间）
    print("\n" + "=" * 60)
    print("开始小规模回填测试...")
    print("注意：这可能需要几分钟时间下载数据")
    print("=" * 60)
    
    import time
    start_time = time.time()
    
    success = test_backfill_small()
    
    elapsed = time.time() - start_time
    print(f"\n回填测试耗时: {elapsed:.1f}秒")
    
    # 测试查询功能
    test_database_query()
    
    # 最终报告
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    db = DatabaseManager()
    stats = db.get_update_stats()
    
    print(f"数据库大小: {stats['database_size_mb']:.2f} MB")
    print(f"日线记录数: {stats['total_daily_records']:,}条")
    print(f"活跃股票数: {stats['total_active_stocks']}支")
    
    # 检查数据库文件
    db_path = db.db_path
    if os.path.exists(db_path):
        size_mb = os.path.getsize(db_path) / (1024 * 1024)
        print(f"数据库文件: {db_path}")
        print(f"文件大小: {size_mb:.2f} MB")
    
    print("\n✅ 所有测试完成!")
    print("\n下一步:")
    print("1. 运行完整回填: python3 backfill_all_stocks.py")
    print("2. 设置每日更新: python3 daily_update.py --create-cron")
    print("3. 更新DataPipeline优先使用本地数据库")

if __name__ == "__main__":
    import datetime
    main()