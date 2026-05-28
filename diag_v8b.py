"""单独查000030和000045"""
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

for ts_code in ['000030.SZ', '000045.SZ']:
    df = get_daily_quotes(ts_code, date(2026, 3, 30), date(2026, 4, 7))
    print(f"\n{ts_code}:")
    for _, r in df.iterrows():
        d = str(r['trade_date'])[:10]
        print(f"  {d}: open={float(r['open']):.2f} high={float(r['high']):.2f} low={float(r['low']):.2f} close={float(r['close']):.2f} pct={float(r['pct_chg']):+.2f}%")
