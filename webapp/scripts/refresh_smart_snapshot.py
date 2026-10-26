# -*- coding: utf-8 -*-
"""刷新全市场智能评分表 ``app_smart_snapshot``（约 30~40 秒）。

建议在每日收盘后跑一次（或由定时任务调用）：

    python webapp/scripts/refresh_smart_snapshot.py

API 首次没有物化表时会退回实时 SQL；有表后智能筛选 2~5 秒内返回。
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.services import smart_scan  # noqa: E402


def main() -> int:
    t0 = time.time()
    n = smart_scan.refresh_snapshot()
    print(f"app_smart_snapshot refreshed: {n} rows, {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
