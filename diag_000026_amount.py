"""查000026在4/10-4/15的成交额 - 详细"""
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

df = get_daily_quotes('000026.SZ', date(2026, 4, 7), date(2026, 4, 15))
print("Columns:", list(df.columns))
print("Shape:", df.shape)
print()
for _, r in df.iterrows():
    d = str(r['trade_date'])[:10]
    print(f"  {d}: close={r['close']} amount={r.get('amount','N/A')} volume={r.get('volume','N/A')} turnover={r.get('turnover_rate','N/A')} pct={r.get('pct_chg','N/A')}")
