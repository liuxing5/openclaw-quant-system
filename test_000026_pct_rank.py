"""查4/10和4/13涨幅前50的门槛，以及000026的涨幅排名"""
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

for td, label in [(date(2026,4,10), '4/10'), (date(2026,4,13), '4/13'), (date(2026,4,15), '4/15')]:
    # 涨幅前50
    cur.execute("""
        SELECT ts_code, pct_chg FROM daily_quotes
        WHERE trade_date = %s AND amount > 50000000 AND pct_chg > -5.0
        ORDER BY pct_chg DESC
        LIMIT 50
    """, (td,))
    rows = cur.fetchall()
    min_pct = float(rows[-1][1])
    print(f"\n{label} 涨幅前50: 最低涨幅={min_pct:+.2f}%")
    
    # 000026的涨幅排名
    cur.execute("""
        SELECT COUNT(*) + 1 as rank FROM daily_quotes
        WHERE trade_date = %s AND amount > 50000000 AND pct_chg > -5.0
          AND pct_chg > (SELECT pct_chg FROM daily_quotes WHERE ts_code = '000026.SZ' AND trade_date = %s)
    """, (td, td))
    rank = cur.fetchone()[0]
    
    cur.execute("""
        SELECT pct_chg FROM daily_quotes WHERE ts_code = '000026.SZ' AND trade_date = %s
    """, (td,))
    row = cur.fetchone()
    pct = float(row[0]) if row else 0
    print(f"  000026.SZ: pct={pct:+.2f}%, 涨幅排名约#{rank}")

cur.close()
conn.close()
