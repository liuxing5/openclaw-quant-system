import sys
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')
import os
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '6543'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

print('Step 1: importing meta_engine...')
sys.stdout.flush()
from strategies.meta_strategy import meta_engine
print('Step 1 OK')
sys.stdout.flush()

print('Step 2: importing fast_backtester...')
sys.stdout.flush()
from strategies.meta_strategy import fast_backtester
print('Step 2 OK')
sys.stdout.flush()

print('Step 3: running backtest...')
sys.stdout.flush()
fast_backtester.run_backtest()
print('Step 3 OK')
sys.stdout.flush()
