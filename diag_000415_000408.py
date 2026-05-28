"""查000415和000408的K线"""
import sys, os
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from strategies.meta_strategy.db_data_adapter import get_daily_quotes
from datetime import date

for ts_code, start, end in [
    ('000415.SZ', '2026-04-28', '2026-05-07'),
    ('000408.SZ', '2026-04-18', '2026-04-25'),
]:
    df = get_daily_quotes(ts_code, date(*[int(x) for x in start.split('-')]),
                          date(*[int(x) for x in end.split('-')]))
    print(f"\n{ts_code}:")
    for _, r in df.iterrows():
        d = str(r['trade_date'])[:10]
        o = float(r['open'])
        h = float(r['high'])
        l = float(r['low'])
        c = float(r['close'])
        pct = float(r.get('pct_chg', 0))
        amt = float(r.get('amount', 0))
        print(f"  {d}: open={o:.2f} high={h:.2f} low={l:.2f} close={c:.2f} pct={pct:+.2f}% amount={amt/1e8:.2f}亿")
