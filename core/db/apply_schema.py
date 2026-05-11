"""Apply database schema safely.

Handles existing tables and columns gracefully.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db
from core.utils.env import load_project_env

load_project_env()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def apply_schema():
    """Apply schema.sql in one shot.

    psycopg2 的 cursor.execute() 原生支持单次执行多条以分号分隔的语句，
    比手写 split(';') 切分更鲁棒：不会被 DO $$ ... $$ 块、字符串内分号、
    -- 注释里的分号搞坏。
    schema.sql 里所有 DDL 都用了 IF NOT EXISTS，重复跑安全；不再用
    "已存在就吞错"的兜底——遇到真错误必须 fail-fast，不然会出现"表/列
    没建成功但 apply 显示绿"的假阳性。
    """
    conn = get_db()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        with open(os.path.join(BASE_DIR, 'schema.sql'), 'r', encoding='utf-8') as f:
            schema_sql = f.read()
        cur.execute(schema_sql)
        print("✓ schema.sql applied")
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    apply_schema()
