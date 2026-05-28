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

# 检查5月22日4只持仓的收盘价
for code in ['688360.SH', '300634.SZ', '688507.SH', '301120.SZ']:
    cur.execute("SELECT ts_code, close, open FROM daily_quotes WHERE ts_code=%s AND trade_date=%s",
                (code, '2026-05-22'))
    row = cur.fetchone()
    print(f"{code}: close={row[1] if row else 'N/A'}, open={row[2] if row else 'N/A'}")

# 也检查5月21日
print("\n5月21日:")
for code in ['688360.SH', '300634.SZ', '688507.SH', '301120.SZ']:
    cur.execute("SELECT ts_code, close, open FROM daily_quotes WHERE ts_code=%s AND trade_date=%s",
                (code, '2026-05-21'))
    row = cur.fetchone()
    print(f"{code}: close={row[1] if row else 'N/A'}, open={row[2] if row else 'N/A'}")

conn.close()
