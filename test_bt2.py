import sys
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')
import os

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
