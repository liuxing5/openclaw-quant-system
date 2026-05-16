import os
import psycopg2
from core.db.connection import get_db_fresh

conn = None
try:
    conn = get_db_fresh()
    cur = conn.cursor()

    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = cur.fetchall()
    print('=== 现有表 ===')
    for t in tables:
        print(f'  - {t[0]}')

    print()
    for t in tables:
        table_name = t[0]
        cur.execute('SELECT COUNT(*) FROM %s' % psycopg2.extensions.quote_ident(table_name, conn))
        count = cur.fetchone()[0]

        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
        """, (table_name,))
        cols = cur.fetchall()
        col_info = ', '.join([f'{c[0]}({c[1]})' for c in cols])
        print(f'{table_name}: {count} 条记录')
        print(f'  列: {col_info}')
        print()

    cur.close()
finally:
    if conn and not conn.closed:
        conn.close()
