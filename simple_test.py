#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/.openclaw/workspace/skills/quant/lib')

import time
from datetime import datetime, timedelta

# 使用虚拟环境
import os
venv_python = "/root/.openclaw/workspace/quant_venv/bin/python"
if os.path.exists(venv_python):
    os.execv(venv_python, [venv_python] + sys.argv)

from datasource import DataSourceManager

print("=== 简单双数据源测试 ===")

# 创建管理器
manager = DataSourceManager({
    'priority': ['baostock', 'akshare'],
    'cache_enabled': False
})

# 检查状态
status = manager.get_status()
print(f"数据源状态: Baostock={status['baostock']['available']}, AKShare={status['akshare']['available']}")

# 测试数据获取
end_date = datetime.now().strftime("%Y-%m-%d")
start_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

print(f"\n获取数据 ({start_date} 到 {end_date})...")

symbols = ['600519.SH', '000001.SZ']

for symbol in symbols:
    print(f"\n{symbol}:")
    try:
        start_time = time.time()
        df = manager.get_daily(symbol, start_date, end_date, 'qfq')
        elapsed = time.time() - start_time
        
        if df is not None and len(df) > 0:
            print(f"  成功: {len(df)} 行数据, 耗时 {elapsed:.2f} 秒")
            print(f"  列: {list(df.columns)}")
            print(f"  最新收盘价: {df['close'].iloc[-1] if 'close' in df.columns else 'N/A'}")
        else:
            print("  失败: 无数据")
    except Exception as e:
        print(f"  异常: {e}")

# 清理
manager.cleanup()
print("\n测试完成")