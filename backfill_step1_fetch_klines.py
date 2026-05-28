"""
Step 1: 获取 K 线数据并保存到本地文件
=====================================
按月获取 hs300+zz500 的 K 线数据，保存到 data/klines/ 目录。
每只股票一个文件，避免单次运行时间过长。

用法:
  python backfill_step1_fetch_klines.py --month 2026-01
  python backfill_step1_fetch_klines.py --month 2026-02
  ...
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

FIELDS_K = "date,code,open,high,low,close,volume,amount,turn,pctChg,isST"
DATA_DIR = Path(__file__).parent / "data" / "klines"


def fetch_kline_subprocess(code: str, start_date: str, end_date: str, timeout: int = 20) -> list | None:
    script = (
        "import baostock as bs\n"
        "import json\n"
        "import sys\n"
        "try:\n"
        "    lg = bs.login()\n"
        "    if lg.error_code != '0':\n"
        "        sys.exit(1)\n"
        f"    rs = bs.query_history_k_data_plus('{code}', '{FIELDS_K}', start_date='{start_date}', end_date='{end_date}', frequency='d', adjustflag='3')\n"
        "    if rs.error_code != '0':\n"
        "        bs.logout()\n"
        "        sys.exit(1)\n"
        "    rows = []\n"
        "    while rs.next():\n"
        "        rows.append(dict(zip(rs.fields, rs.get_row_data())))\n"
        "    bs.logout()\n"
        "    print(json.dumps(rows))\n"
        "except Exception:\n"
        "    try:\n"
        "        bs.logout()\n"
        "    except:\n"
        "        pass\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, '-c', script],
            capture_output=True, text=True, timeout=timeout,
            encoding='utf-8', errors='replace',
        )
        for line in reversed(result.stdout.strip().split('\n')):
            line = line.strip()
            if line.startswith('['):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
                break
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def get_stock_pool() -> list[str]:
    script = (
        "import baostock as bs\n"
        "import json\n"
        "import sys\n"
        "try:\n"
        "    lg = bs.login()\n"
        "    if lg.error_code != '0':\n"
        "        sys.exit(1)\n"
        "    rs = bs.query_hs300_stocks()\n"
        "    hs300 = set()\n"
        "    while rs.next():\n"
        "        hs300.add(rs.get_row_data()[1])\n"
        "    rs = bs.query_zz500_stocks()\n"
        "    zz500 = set()\n"
        "    while rs.next():\n"
        "        zz500.add(rs.get_row_data()[1])\n"
        "    bs.logout()\n"
        "    print(json.dumps(sorted(hs300 | zz500)))\n"
        "except Exception:\n"
        "    sys.exit(1)\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, '-c', script],
            capture_output=True, text=True, timeout=30,
            encoding='utf-8', errors='replace',
        )
        for line in reversed(result.stdout.strip().split('\n')):
            if line.strip().startswith('['):
                try:
                    return json.loads(line.strip())
                except json.JSONDecodeError:
                    pass
                break
        return []
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", type=str, required=True, help="e.g. 2026-01")
    parser.add_argument("--timeout", type=int, default=20, help="per-stock timeout seconds")
    args = parser.parse_args()

    year, month = args.month.split('-')
    year, month = int(year), int(month)
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)

    kline_start = (month_start - timedelta(days=60)).strftime("%Y-%m-%d")
    kline_end = month_end.strftime("%Y-%m-%d")

    month_dir = DATA_DIR / args.month
    month_dir.mkdir(parents=True, exist_ok=True)

    log_file = month_dir / "fetch_log.txt"
    log_fp = open(log_file, 'w', encoding='utf-8')

    def log(msg):
        print(msg, flush=True)
        log_fp.write(msg + '\n')
        log_fp.flush()

    log("=" * 60)
    log(f"  K-line fetch: {args.month}")
    log(f"  Range: {kline_start} ~ {kline_end}")
    log(f"  Dir: {month_dir}")
    log("=" * 60)

    codes = get_stock_pool()
    if not codes:
        log("ERROR: stock pool fetch failed!")
        log_fp.close()
        sys.exit(1)
    log(f"Stock pool: {len(codes)} codes")

    existing = set(f.stem for f in month_dir.glob("*.json"))
    if existing:
        log(f"Already have {len(existing)} stocks")

    todo = [c for c in codes if c not in existing]
    if not todo:
        log("All stocks already fetched!")
        log_fp.close()
        return

    log(f"Need to fetch {len(todo)} stocks...")

    ok_count = len(existing)
    fail_count = 0
    t0 = time.time()

    for i, code in enumerate(todo):
        try:
            data = fetch_kline_subprocess(code, kline_start, kline_end, timeout=args.timeout)
        except Exception as e:
            log(f"  ERR {code}: {e}")
            data = None
        if data and len(data) > 0:
            filepath = month_dir / f"{code}.json"
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            ok_count += 1
        else:
            fail_count += 1

        if (i + 1) % 10 == 0 or i == len(todo) - 1:
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(todo) - i - 1)
            log(f"  [{i+1}/{len(todo)}] ok={ok_count} fail={fail_count} ({elapsed:.0f}s, ETA {eta:.0f}s)")

    elapsed = time.time() - t0
    log(f"\nDone! ok={ok_count} fail={fail_count} ({elapsed:.0f}s)")
    log_fp.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"\nFATAL: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
