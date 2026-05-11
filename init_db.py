"""Initialize database schema for the Quant System.

Thin wrapper around core.db.apply_schema for one-shot bootstrap.
Run once after configuring .env with POSTGRES_*.
"""
import sys
from core.db.apply_schema import apply_schema

if __name__ == '__main__':
    try:
        apply_schema()
        print("\n✅ 数据库初始化完成！")
    except Exception as e:
        print(f"\n❌ 数据库初始化失败: {e}")
        sys.exit(1)
