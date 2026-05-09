import psycopg2

conn = psycopg2.connect(
    'postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres'
)
cur = conn.cursor()

# 查看所有表
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

# 查看每个表的记录数和结构
print()
for t in tables:
    table_name = t[0]
    cur.execute(f'SELECT COUNT(*) FROM {table_name}')
    count = cur.fetchone()[0]

    cur.execute(f"""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = '{table_name}'
        ORDER BY ordinal_position
    """)
    cols = cur.fetchall()
    col_info = ', '.join([f'{c[0]}({c[1]})' for c in cols])
    print(f'{table_name}: {count} 条记录')
    print(f'  列: {col_info}')
    print()

conn.close()
