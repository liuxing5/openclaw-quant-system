#!/usr/bin/env python3
"""判断 latest_1430.json 是否已经是「当日 14:30 盘中窗口」的真实快照。

凌晨补跑（例如 GitHub cron 延迟到 01:30 才触发）虽然 trade_date 是当天，
但 scan_completed_at 在凌晨，不能作为 14:30 快照使用；否则当日真正的
14:29/14:31 触发会被幂等判断误跳过。

退出码 0  =>  已扫描，skip
退出码 1  =>  需要扫描
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

INTRADAY_START = (13, 0)   # 允许 GitHub cron 最多提前/延迟到 13:00
INTRADAY_END = (15, 10)    # 允许盘后一点点延迟，但不能是收盘后重算


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="交易日 YYYY-MM-DD")
    ap.add_argument("--file", default="docs/overnight_v2/latest_1430.json")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print("no snapshot file -> need scan")
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"unreadable snapshot ({exc}) -> need scan")
        return 1
    if str(data.get("trade_date") or "") != args.date:
        print(f"snapshot date {data.get('trade_date')} != {args.date} -> need scan")
        return 1

    raw = str(data.get("scan_completed_at") or "")
    try:
        completed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except Exception:
        print(f"invalid scan_completed_at {raw!r} -> need scan")
        return 1
    if completed.strftime("%Y-%m-%d") != args.date:
        print(f"snapshot completed {completed} != {args.date} -> need scan")
        return 1

    hm = (completed.hour, completed.minute)
    if INTRADAY_START <= hm <= INTRADAY_END:
        print(f"intraday snapshot completed at {completed} -> skip")
        return 0
    print(f"snapshot completed at {completed} (overnight/backfill) -> need scan")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
