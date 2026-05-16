"""清理 daily_quotes 表中的重复数据"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db_fresh
from core.utils.env import load_project_env

load_project_env()

conn = None
try:
    conn = get_db_fresh()
    conn.autocommit = True

    cur = conn.cursor()

    cur.execute("""
        SELECT ts_code, trade_date, COUNT(*) as cnt
        FROM daily_quotes
        GROUP BY ts_code, trade_date
        HAVING COUNT(*) > 1
        ORDER BY trade_date DESC
        LIMIT 10;
    """)
    dupes = cur.fetchall()
    print(f"发现 {len(dupes)} 组重复数据:")
    for d in dupes:
        print(f"  {d[0]} {d[1]} - {d[2]} 条")

    cur.execute("""
        DELETE FROM daily_quotes
        WHERE trade_date = '2026-05-08'
          AND ctid NOT IN (
              SELECT MIN(ctid)
              FROM daily_quotes
              WHERE trade_date = '2026-05-08'
              GROUP BY ts_code
          );
    """)
    deleted = cur.rowcount
    print(f"\n删除了 {deleted} 条 2026-05-08 的重复数据")

    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'daily_quotes_pkey'
            ) THEN
                ALTER TABLE daily_quotes ADD PRIMARY KEY (ts_code, trade_date);
            END IF;
        END $$;
    """)
    print("主键约束已确保存在")

    cur.close()
finally:
    if conn and not conn.closed:
        conn.close()
print("完成")
