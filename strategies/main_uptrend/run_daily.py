"""
主升浪检测策略 — 日频运行入口
=================================
用法:
  # 盘后运行（15:10+）
  python -m strategies.main_uptrend.run_daily

  # 指定日期
  python -m strategies.main_uptrend.run_daily --date 2026-05-15

  # 跳过 A 层预筛
  python -m strategies.main_uptrend.run_daily --no-a
"""
from __future__ import annotations

import argparse
import logging
import sys
import os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from strategies.main_uptrend.config import MainUptrendConfig
from strategies.main_uptrend.engine import MainUptrendEngine
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def main():
    parser = argparse.ArgumentParser(
        description="主升浪检测策略 — 日频运行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行时机: 每日 15:10 盘后（数据稳定后）

示例:
  python -m strategies.main_uptrend.run_daily
  python -m strategies.main_uptrend.run_daily --date 2026-05-15
  python -m strategies.main_uptrend.run_daily --no-a --no-d
        """
    )
    parser.add_argument("--date", "-d", type=str, default=None,
                        help="评估日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--no-a", action="store_true",
                        help="跳过 A 层预筛")
    parser.add_argument("--no-c", action="store_true",
                        help="跳过 C 层持续性判定")
    parser.add_argument("--no-d", action="store_true",
                        help="跳过 D 层风险过滤")
    parser.add_argument("--write-db", action="store_true", default=True,
                        help="写入 daily_candidates 表")
    parser.add_argument("--no-write-db", action="store_true",
                        help="不写入数据库")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出文件路径")

    args = parser.parse_args()

    cfg = MainUptrendConfig(
        a_enabled=not args.no_a,
        c_enabled=not args.no_c,
        d_enabled=not args.no_d,
    )

    eval_date = args.date or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    engine = MainUptrendEngine(cfg)
    result = engine.run_daily(eval_date)

    print("\n" + "=" * 60)
    print(f"主升浪检测 — {eval_date}")
    print("=" * 60)
    print(f"A 层预筛池: {result['a_pool_size']} 只")
    print(f"B 层启动信号: {result['b_signals']} 只")
    print(f"C 层持续性: {result['c_signals']} 只")
    print(f"D 层通过: {result['d_passed']} 只")
    print(f"最终候选: {len(result['candidates'])} 只")
    print("=" * 60)

    if result['candidates']:
        print("\n--- 候选标的 ---")
        for i, c in enumerate(result['candidates'], 1):
            print(f"\n  {i}. {c['ts_code']}")
            print(f"     B层分: {c['b_score']:.1f}  C层分: {c['c_score']:.1f}")
            b_details = c.get('b_details', {})
            c_details = c.get('c_details', {})
            for k, v in b_details.items():
                if v:
                    print(f"     [{k}] {v}")
            for k, v in c_details.items():
                if v:
                    print(f"     [{k}] {v}")
    else:
        print("\n无候选标的")

    # 写入数据库
    if args.write_db and not args.no_write_db and result['candidates']:
        engine.write_to_db(result['candidates'], run_mode="afternoon")
        print(f"\n✅ 已写入 daily_candidates (source=main_uptrend)")

    # 输出文件
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(f"主升浪检测 {eval_date}\n")
            f.write(f"A:{result['a_pool_size']} B:{result['b_signals']} C:{result['c_signals']} D:{result['d_passed']}\n")
            for c in result['candidates']:
                f.write(f"{c['ts_code']} B={c['b_score']:.1f} C={c['c_score']:.1f}\n")


if __name__ == "__main__":
    main()