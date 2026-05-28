import psycopg2
conn = psycopg2.connect(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=5432,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
    connect_timeout=30,
)
cur = conn.cursor()

pos_codes = ['688360.SH', '300634.SZ', '688507.SH', '301120.SZ']
cur.execute("""
    SELECT ts_code, close FROM daily_quotes
    WHERE trade_date = %s AND ts_code = ANY(%s)
""", ('2026-05-22', pos_codes))
rows = cur.fetchall()
print(f"Found {len(rows)} rows:")
for r in rows:
    print(f"  {r[0]}: close={r[1]}")

conn.close()
