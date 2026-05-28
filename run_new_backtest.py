import sys
import os

# 确保使用正确的Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 删除旧的输出文件
out_file = "diag_key_stocks_out.txt"
if os.path.exists(out_file):
    os.remove(out_file)

# 导入并运行回测
from backtest_t_plus1 import run_backtest

print("开始回测...")
run_backtest(mode="both", max_hold=None, actual_start_date="2025-05-01")
print("回测完成！")
