import sys
sys.path.insert(0, '.')
try:
    from strategies.meta_strategy.fast_backtester import FastBacktester
    print('import_ok')
except Exception as e:
    print(f'import_error: {e}')
