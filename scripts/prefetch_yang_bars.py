# -*- coding: utf-8 -*-
"""并行预取严格版回测所需的 5 分钟 K 线，缓存到 pickle。"""
from __future__ import annotations

import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webapp" / "backend"))
sys.path.insert(0, str(ROOT))

import baostock as bs  # noqa: E402

from app.db import query  # noqa: E402
from scripts.backtest_overnight_classic import _bs_code, _daily_candidates, _fetch, _index_code  # noqa: E402


def _init():
    bs.login()


def _work(task):
    code, start, end = task
    return task, _fetch(code, start, end)


def main():
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=186)).isoformat()
    cal = [r["trade_date"].isoformat() for r in query("SELECT DISTINCT trade_date FROM daily_quotes WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date", (start, end))]
    start, end = cal[0], cal[-1]
    candidates = _daily_candidates(start, end)
    by_day = {}
    for c in candidates:
        by_day.setdefault(c["trade_date"].isoformat(), []).append(c)
    next_day = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    tasks = []
    for d, items in by_day.items():
        nd = next_day.get(d)
        if not nd:
            continue
        s = (date.fromisoformat(d) - timedelta(days=2)).isoformat()
        e = (date.fromisoformat(nd) + timedelta(days=2)).isoformat()
        for c in items:
            tasks.append((_bs_code(c["ts_code"]), s, e))
    for idx in ("sh.000001", "sz.399001"):
        tasks.append((idx, start, end))
    # 去重
    tasks = list(dict.fromkeys(tasks))
    print("candidates", len(candidates), "tasks", len(tasks), "codes", len({t[0] for t in tasks}), flush=True)
    cache = {}
    with ProcessPoolExecutor(max_workers=6, initializer=_init) as ex:
        for i, (task, bars) in enumerate(ex.map(_work, tasks, chunksize=2), 1):
            cache[task] = bars
            if i % 25 == 0:
                print(f"  {i}/{len(tasks)}", flush=True)
    out = ROOT / "reports/yang_strict_bars.pkl"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(pickle.dumps(cache, protocol=4))
    print("saved", out, "tasks", len(cache), flush=True)


if __name__ == "__main__":
    main()
