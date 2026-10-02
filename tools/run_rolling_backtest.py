#!/usr/bin/env python3
"""Rolling out-of-sample backtest runner (Phase 1 framework).

对现有策略回测入口做滚动窗口调用，避免一次性静态回测。
Usage:
  python tools/run_rolling_backtest.py --start 2024-01-01 --end 2026-05-01 \
      --window-months 3 --step-months 1

默认跑 main_uptrend 的 run_backtest；可用 --cmd 换其它策略回测入口。
"""
import argparse
import datetime as dt
import subprocess
import sys

DEFAULT_CMD = "python -m strategies.main_uptrend.run_backtest --no-a"


def windows(start, end, win_months, step_months):
    y, m, d = map(int, start.split("-"))
    cur = dt.date(y, m, d)
    y2, m2, d2 = map(int, end.split("-"))
    last = dt.date(y2, m2, d2)
    while True:
        nxt_year = cur.year + (cur.month + win_months - 1) // 12
        nxt_month = (cur.month + win_months - 1) % 12 + 1
        try:
            probe = cur.replace(year=nxt_year, month=nxt_month)
        except ValueError:
            probe = cur.replace(year=nxt_year, month=nxt_month, day=28)
        if probe > last:
            break
        yield cur.isoformat(), probe.isoformat()
        cur = cur.replace(year=cur.year + (cur.month + step_months - 1) // 12,
                          month=(cur.month + step_months - 1) % 12 + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--window-months", type=int, default=3)
    ap.add_argument("--step-months", type=int, default=1)
    ap.add_argument("--cmd", default=DEFAULT_CMD,
                    help="策略回测入口命令模板，必须支持 --start/--end/--output")
    args = ap.parse_args()

    for s, e in windows(args.start, args.end, args.window_months, args.step_months):
        print(f"\n===== OUT-OF-SAMPLE: {s} ~ {e} =====", flush=True)
        cmd = f"{args.cmd} --start {s} --end {e} --output /tmp/rolling_backtest.txt"
        try:
            subprocess.run(cmd, shell=True, check=True, timeout=3600)
        except subprocess.CalledProcessError as exc:
            print(f"窗口 {s}~{e} 失败，退出码 {exc.returncode}，继续下一窗口")
        except subprocess.TimeoutExpired:
            print(f"窗口 {s}~{e} 超时")


if __name__ == "__main__":
    main()
