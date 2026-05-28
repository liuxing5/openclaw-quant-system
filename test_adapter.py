"""Test data adapter directly"""
import sys, os, time
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from datetime import date

print('Importing adapter...')
sys.stdout.flush()

from strategies.meta_strategy.db_data_adapter import (
    get_trading_days, get_daily_quotes_for_date, get_market_overview
)

print('Testing get_trading_days...')
sys.stdout.flush()
t0 = time.time()
days = get_trading_days(date(2026, 4, 1), date(2026, 5, 15))
print(f'Got {len(days)} trading days in {time.time()-t0:.1f}s')
sys.stdout.flush()

print('Testing get_market_overview...')
sys.stdout.flush()
t0 = time.time()
ov = get_market_overview(date(2026, 4, 1))
print(f'Overview: {ov} in {time.time()-t0:.1f}s')
sys.stdout.flush()

print('Testing get_daily_quotes_for_date (first day)...')
sys.stdout.flush()
t0 = time.time()
df = get_daily_quotes_for_date(date(2026, 4, 1))
print(f'Got {len(df)} rows in {time.time()-t0:.1f}s')
sys.stdout.flush()

print('ALL TESTS PASSED')
sys.stdout.flush()
