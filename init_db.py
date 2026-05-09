"""Initialize database schema for the Quant System.

Run this once to create all required tables.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.utils.env import load_project_env
from core.db.connection import get_db

load_project_env()

def init_database():
    print("=== 初始化数据库 ===")
    
    # 读取 schema.sql 文件
    schema_path = os.path.join(os.path.dirname(__file__), 'core', 'db', 'schema.sql')
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema_sql = f.read()
    
    # 分割 SQL 语句（按分号分隔）
    statements = schema_sql.split(';')
    
    try:
        conn = get_db()
        cur = conn.cursor()
        
        for i, stmt in enumerate(statements):
            stmt = stmt.strip()
            if stmt and not stmt.startswith('--'):
                try:
                    cur.execute(stmt)
                    print(f"✓ 执行语句 {i+1}")
                except Exception as e:
                    print(f"⚠️ 语句 {i+1} 执行失败（可能已存在）: {e}")
        
        conn.commit()
        cur.close()
        conn.close()
        print("\n✅ 数据库初始化完成！")
        
    except Exception as e:
        print(f"\n❌ 数据库连接失败: {e}")
        sys.exit(1)

if __name__ == '__main__':
    init_database()
