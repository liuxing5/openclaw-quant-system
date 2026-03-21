#!/usr/bin/env python3
import sys
import time
import traceback

# 使用虚拟环境
import os
venv_python = "/root/.openclaw/workspace/quant_venv/bin/python"
if os.path.exists(venv_python):
    os.execv(venv_python, [venv_python] + sys.argv)

print("=== 调试数据源 ===")

# 1. 测试导入
print("1. 导入模块...")
start = time.time()
try:
    from datasource import DataSourceManager
    print(f"   导入成功，耗时 {time.time() - start:.2f} 秒")
except Exception as e:
    print(f"   导入失败: {e}")
    traceback.print_exc()
    sys.exit(1)

# 2. 测试初始化
print("2. 初始化管理器...")
start = time.time()
try:
    manager = DataSourceManager({'cache_enabled': False})
    print(f"   初始化成功，耗时 {time.time() - start:.2f} 秒")
except Exception as e:
    print(f"   初始化失败: {e}")
    traceback.print_exc()
    sys.exit(1)

# 3. 测试状态
print("3. 获取状态...")
status = manager.get_status()
print(f"   状态: {status}")

# 4. 测试 Baostock 连接
print("4. 测试 Baostock 直接连接...")
start = time.time()
try:
    import baostock as bs
    lg = bs.login()
    print(f"   Baostock 登录: {lg.error_msg}，耗时 {time.time() - start:.2f} 秒")
    bs.logout()
except Exception as e:
    print(f"   Baostock 错误: {e}")

# 5. 测试 AKShare 连接
print("5. 测试 AKShare 直接连接...")
start = time.time()
try:
    import akshare as ak
    print(f"   AKShare 导入成功，耗时 {time.time() - start:.2f} 秒")
except Exception as e:
    print(f"   AKShare 导入错误: {e}")

# 6. 清理
manager.cleanup()
print("\n调试完成")