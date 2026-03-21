#!/usr/bin/env python3
"""
测试数据库并发访问
"""

import threading
import time
from data.database.database_manager import DatabaseManager

def worker(stock_id, results):
    """工作线程：模拟并发数据库访问"""
    db = DatabaseManager()
    
    try:
        # 模拟股票信息插入
        symbol = f"TEST{stock_id:04d}"
        db.upsert_stock(
            symbol=symbol,
            name=f"测试股票{stock_id}",
            market="SZ" if stock_id % 2 == 0 else "SH",
            industry="测试行业",
            status="active"
        )
        
        # 模拟价格数据插入
        import pandas as pd
        import numpy as np
        
        dates = pd.date_range(start='2025-01-01', end='2025-01-05', freq='B')
        df = pd.DataFrame({
            'open': np.random.normal(100, 10, len(dates)),
            'high': np.random.normal(105, 10, len(dates)),
            'low': np.random.normal(95, 10, len(dates)),
            'close': np.random.normal(100, 10, len(dates)),
            'volume': np.random.randint(1000000, 10000000, len(dates))
        }, index=dates)
        
        new_count, update_count = db.insert_daily_prices(symbol, df, data_source='test')
        
        results[stock_id] = {
            'success': True,
            'symbol': symbol,
            'new_data': new_count,
            'update_count': update_count
        }
        
    except Exception as e:
        results[stock_id] = {
            'success': False,
            'error': str(e)
        }

def test_concurrency(num_workers=4):
    """并发测试"""
    print(f"开始并发测试，{num_workers}个线程...")
    
    threads = []
    results = {}
    
    # 启动线程
    for i in range(num_workers):
        t = threading.Thread(target=worker, args=(i, results))
        threads.append(t)
        t.start()
    
    # 等待所有线程完成
    for t in threads:
        t.join()
    
    # 统计结果
    success_count = sum(1 for r in results.values() if r.get('success', False))
    fail_count = num_workers - success_count
    
    print(f"并发测试完成:")
    print(f"  成功: {success_count}")
    print(f"  失败: {fail_count}")
    
    if fail_count > 0:
        print("失败详情:")
        for i, result in results.items():
            if not result.get('success', False):
                print(f"  线程{i}: {result.get('error', '未知错误')}")
    
    return success_count == num_workers

if __name__ == "__main__":
    # 先清理测试数据
    db = DatabaseManager()
    
    # 删除可能的测试数据
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM stocks WHERE symbol LIKE 'TEST%'")
        cursor.execute("DELETE FROM daily_prices WHERE symbol LIKE 'TEST%'")
        conn.commit()
    
    print("清理测试数据完成")
    
    # 运行并发测试
    success = test_concurrency(4)
    
    if success:
        print("\n✅ 并发测试通过！数据库锁定问题已解决。")
    else:
        print("\n❌ 并发测试失败，需要进一步优化。")
    
    # 显示最终统计
    stats = db.get_update_stats()
    print(f"\n数据库统计:")
    print(f"  活跃股票数: {stats['total_active_stocks']}")
    print(f"  日线记录数: {stats['total_daily_records']:,}")
    print(f"  数据库大小: {stats['database_size_mb']:.2f} MB")