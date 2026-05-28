"""
回填 funnel_strategy 策略数据到 daily_candidates 表
====================================================
用法:
  python backfill_funnel_strategy.py --start 2026-01-01 --end 2026-05-27
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from datetime import date, datetime

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.utils.env import load_project_env
load_project_env()

from core.utils.trading_calendar import get_trading_days_in_range
from core.db.connection import get_db, close_db_session


def backfill_funnel(start: date, end: date, skip_existing: bool = True):
    """批量回填 funnel_strategy 数据"""
    from strategies.funnel_strategy.funnel_engine import FunnelEngine, DEFAULT_FUNNEL_CONFIG

    # 获取交易日期
    trade_dates = get_trading_days_in_range(start, end)
    print(f"交易日: {len(trade_dates)} 天 ({start} ~ {end})")

    # 检查已存在的日期
    existing_dates = set()
    if skip_existing:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT snapshot_date FROM daily_candidates 
            WHERE source = 'funnel_strategy' AND snapshot_date >= %s AND snapshot_date <= %s
        """, (start, end))
        for row in cur.fetchall():
            existing_dates.add(row[0])
        cur.close()
        close_db_session()
        print(f"已存在: {len(existing_dates)} 天")

    # 过滤需要处理的日期
    dates_to_process = [d for d in trade_dates if d not in existing_dates]
    print(f"待处理: {len(dates_to_process)} 天")

    cfg = DEFAULT_FUNNEL_CONFIG
    cfg.verbose = False

    total_written = 0
    t0 = time.time()

    for i, td in enumerate(dates_to_process, 1):
        t1 = time.time()
        try:
            engine = FunnelEngine(cfg)
            result = engine.run(trade_date=td)
            candidates = result.get('candidates', [])
            total_written += len(candidates)
            elapsed = time.time() - t1
            status = f"{len(candidates)} 条" if candidates else "无推荐"
            print(f"  [{i}/{len(dates_to_process)}] {td}: {status} ({elapsed:.1f}s)")
        except Exception as e:
            print(f"  [{i}/{len(dates_to_process)}] {td}: 错误 - {e}")

    total_elapsed = time.time() - t0
    print(f"\n完成! 总写入: {total_written} 条, 耗时: {total_elapsed:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填 funnel_strategy 策略数据")
    parser.add_argument("--start", type=str, required=True, help="开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, required=True, help="结束日期 (YYYY-MM-DD)")
    parser.add_argument("--no-skip", action="store_true", help="不跳过已存在的日期")
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    print("=" * 60)
    print(f"  回填 funnel_strategy")
    print(f"  日期范围: {start_date} ~ {end_date}")
    print("=" * 60)

    backfill_funnel(start_date, end_date, skip_existing=not args.no_skip)
