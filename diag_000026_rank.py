"""查4/13和4/10成交额排名，看000026是否在前100"""
import sys, os
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from core.db.connection import get_db_fresh
from datetime import date

conn = get_db_fresh()
cur = conn.cursor()

for td, label in [(date(2026,4,10), '4/10'), (date(2026,4,13), '4/13')]:
    cur.execute("""
        SELECT ts_code, amount FROM daily_quotes
        WHERE trade_date = %s AND amount > 50000000
        ORDER BY amount DESC
    """, (td,))
    rows = cur.fetchall()
    print(f"\n{label}: 共{len(rows)}只>5000万")
    # 找000026排名
    for i, (ts_code, amount) in enumerate(rows):
        if ts_code == '000026.SZ':
            print(f"  000026.SZ 排名 #{i+1}, amount={float(amount)/1e8:.2f}亿")
            break
    else:
        print(f"  000026.SZ 不在>5000万列表中")
    # 打印前5和后5
    print(f"  前5名:")
    for i, (ts_code, amount) in enumerate(rows[:5]):
        print(f"    #{i+1}: {ts_code} {float(amount)/1e8:.2f}亿")
    print(f"  第96-105名:")
    for i, (ts_code, amount) in enumerate(rows[95:105]):
        print(f"    #{96+i}: {ts_code} {float(amount)/1e8:.2f}亿")

cur.close()
conn.close()
