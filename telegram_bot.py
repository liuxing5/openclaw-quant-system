"""
Telegram Bot 入口文件（根目录）
Render 从根目录运行 python telegram_bot.py，此文件加载实际模块。
"""
import sys
import os
import importlib.util

_actual_path = os.path.join(os.path.dirname(__file__), "strategies", "overnight_8step", "telegram_bot.py")
sys.path.insert(0, os.path.dirname(_actual_path))

_spec = importlib.util.spec_from_file_location("bot_main", _actual_path)
_bot_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bot_module)

if __name__ == "__main__":
    _bot_module.main()
