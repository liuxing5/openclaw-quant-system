"""
Telegram Bot 入口文件（根目录）
Render 从根目录运行 python telegram_bot.py，通过 runpy 执行实际模块。
"""
import sys
import os

_actual_file = os.path.join(os.path.dirname(__file__), "strategies", "overnight_8step", "telegram_bot.py")

if not os.path.exists(_actual_file):
    print(f"❌ 找不到实际模块: {_actual_file}", flush=True)
    sys.exit(1)

sys.path.insert(0, os.path.dirname(_actual_file))

import runpy
runpy.run_path(_actual_file, run_name="__main__")
