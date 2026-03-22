#!/usr/bin/env python3
"""
数据库诊断测试
"""

import sys
import os
sys.path.append('/root/.openclaw/workspace/quant_system')

from data.database.database_manager import DatabaseManager

def test_table_columns():
    """测试表列是否存在"""
    db = DatabaseManager()
    
    print("检查表列结构...")
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        tables = ['stocks', 'daily_prices', 'index_data', 'financials']
        
        for table in tables:
            try:
                cursor.execute(f"PRAGMA table_info({table})")
                columns = cursor.fetchall()
                col_names = [col[1] for col in columns]
                
                print(f"\n{table}:")
                print(f"  列数: {len(columns)}")
                print(f"  列名: {col_names}")
                
                # 检查updated_at
                if 'updated_at' in col_names:
                    print(f"  ✅ 有updated_at列")
                else:
                    print(f"  ⚠️  缺少updated_at列")
                    
            except Exception as e:
                print(f"\n{table}: 查询失败 - {e}")

def test_stock_operations():
    """测试股票操作"""
    print("\n\n测试股票操作...")
    db = DatabaseManager()
    
    # 测试插入股票
    test_symbol = "TEST9999"
    
    try:
        success = db.upsert_stock(
            symbol=test_symbol,
            name="诊断测试股票",
            market="SZ",
            industry="测试"
        )
        print(f"插入股票 {test_symbol}: {'成功' if success else '失败'}")
    except Exception as e:
        print(f"插入股票失败: {e}")
    
    # 测试获取股票信息
    try:
        info = db.get_stock_info(test_symbol)
        print(f"获取股票信息: {info}")
    except Exception as e:
        print(f"获取股票信息失败: {e}")
    
    # 清理测试数据
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM stocks WHERE symbol = ?", (test_symbol,))
            cursor.execute("DELETE FROM daily_prices WHERE symbol = ?", (test_symbol,))
            conn.commit()
        print("清理测试数据完成")
    except Exception as e:
        print(f"清理失败: {e}")

def test_daily_price_operations():
    """测试日线价格操作"""
    print("\n\n测试日线价格操作...")
    db = DatabaseManager()
    
    import pandas as pd
    import numpy as np
    
    test_symbol = "TEST8888"
    
    # 创建测试数据
    dates = pd.date_range(start='2025-01-01', end='2025-01-03', freq='B')
    df = pd.DataFrame({
        'open': [100, 101, 102],
        'high': [105, 106, 107],
        'low': [95, 96, 97],
        'close': [102, 103, 104],
        'volume': [1000000, 2000000, 3000000],
        'amount': [102000000, 206000000, 312000000]
    }, index=dates)
    
    try:
        # 插入股票信息
        db.upsert_stock(test_symbol, "价格测试股票", "SH")
        
        # 插入价格数据
        new_count, update_count = db.insert_daily_prices(test_symbol, df)
        print(f"插入价格数据: 新增{new_count}条, 更新{update_count}条")
        
        # 查询价格数据
        prices = db.get_daily_prices(test_symbol, '2025-01-01', '2025-01-05')
        print(f"查询价格数据: {len(prices) if prices is not None else 0}条")
        
    except Exception as e:
        print(f"价格操作失败: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # 清理
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM stocks WHERE symbol = ?", (test_symbol,))
            cursor.execute("DELETE FROM daily_prices WHERE symbol = ?", (test_symbol,))
            conn.commit()

def test_index_operations():
    """测试指数操作"""
    print("\n\n测试指数操作...")
    db = DatabaseManager()
    
    import pandas as pd
    
    test_symbol = "TESTINDEX"
    
    # 创建测试数据
    dates = pd.date_range(start='2025-01-01', end='2025-01-02', freq='B')
    df = pd.DataFrame({
        'open': [3000, 3010],
        'high': [3050, 3060],
        'low': [2950, 2960],
        'close': [3020, 3030],
        'volume': [1000000000, 1100000000],
        'amount': [3.02e12, 3.33e12],
        'change': [20, 10],
        'change_pct': [0.67, 0.33]
    }, index=dates)
    
    try:
        count = db.insert_index_data(test_symbol, df)
        print(f"插入指数数据: {count}条")
        
    except Exception as e:
        print(f"指数操作失败: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # 清理
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM index_data WHERE symbol = ?", (test_symbol,))
            conn.commit()

if __name__ == "__main__":
    print("=" * 60)
    print("数据库诊断测试")
    print("=" * 60)
    
    test_table_columns()
    test_stock_operations()
    test_daily_price_operations()
    test_index_operations()
    
    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)