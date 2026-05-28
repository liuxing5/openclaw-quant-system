import sys
import traceback
sys.stdout = open('backtest_output.txt', 'w', encoding='utf-8')
sys.stderr = sys.stdout

try:
    exec(open('backtest_t_plus1.py', encoding='utf-8').read())
except Exception as e:
    print(f"FATAL ERROR: {e}")
    traceback.print_exc()
finally:
    sys.stdout.close()
