#!/usr/bin/env python3
"""
数据库迁移脚本：添加updated_at列
"""

import sqlite3
import os

def migrate_database(db_path=None):
    """迁移数据库，添加缺失的updated_at列"""
    if db_path is None:
        db_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(db_dir, "quant_history.db")
    
    print(f"迁移数据库: {db_path}")
    
    # 检查文件是否存在
    if not os.path.exists(db_path):
        print("数据库文件不存在，无需迁移")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 1. 检查daily_prices表是否有updated_at列
        cursor.execute("PRAGMA table_info(daily_prices)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'updated_at' not in columns:
            print("为daily_prices表添加updated_at列...")
            cursor.execute("""
                ALTER TABLE daily_prices 
                ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            """)
            print("  ✅ daily_prices.updated_at添加成功")
        else:
            print("  ✅ daily_prices已有updated_at列")
        
        # 2. 检查index_data表是否有updated_at列
        cursor.execute("PRAGMA table_info(index_data)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'updated_at' not in columns:
            print("为index_data表添加updated_at列...")
            cursor.execute("""
                ALTER TABLE index_data 
                ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            """)
            print("  ✅ index_data.updated_at添加成功")
        else:
            print("  ✅ index_data已有updated_at列")
        
        # 3. 检查financials表是否有updated_at列
        cursor.execute("PRAGMA table_info(financials)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'updated_at' not in columns:
            print("为financials表添加updated_at列...")
            cursor.execute("""
                ALTER TABLE financials 
                ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            """)
            print("  ✅ financials.updated_at添加成功")
        else:
            print("  ✅ financials已有updated_at列")
        
        # 4. 检查minute_prices表是否有updated_at列
        cursor.execute("PRAGMA table_info(minute_prices)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'updated_at' not in columns:
            print("为minute_prices表添加updated_at列...")
            cursor.execute("""
                ALTER TABLE minute_prices 
                ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            """)
            print("  ✅ minute_prices.updated_at添加成功")
        else:
            print("  ✅ minute_prices已有updated_at列")
        
        conn.commit()
        print("\n✅ 数据库迁移完成!")
        
        # 显示表结构
        print("\n表结构概览:")
        tables = ['stocks', 'daily_prices', 'index_data', 'financials', 'minute_prices']
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table})")
            cols = cursor.fetchall()
            print(f"  {table}: {len(cols)}列")
            # 显示列名
            col_names = [col[1] for col in cols]
            print(f"    列: {', '.join(col_names[:5])}{'...' if len(col_names) > 5 else ''}")
        
        return True
        
    except Exception as e:
        print(f"❌ 迁移失败: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    success = migrate_database()
    if success:
        print("\n迁移成功！现在重新运行批量回填测试。")
    else:
        print("\n迁移失败，请检查错误信息。")