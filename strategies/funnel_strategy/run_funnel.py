"""
实盘运行入口 — 七步漏斗选股策略
================================
Usage:
  # 完整七步漏斗
  python -m strategies.funnel_strategy.run_funnel

  # 指定日期 + 输出目录
  python -m strategies.funnel_strategy.run_funnel --date 2026-05-12 --output ./results

  # 跳过某几层（调试用）
  python -m strategies.funnel_strategy.run_funnel --disable 0 4

  # 复盘昨日推荐
  python -m strategies.funnel_strategy.run_funnel --review

  # 先复盘再选股（完整闭环）
  python -m strategies.funnel_strategy.run_funnel --review --funnel
"""
from __future__ import annotations

import sys
import os
import argparse
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from dotenv import load_dotenv
for _env_path in [Path('.env'), Path('strategies/llm_multisource/.env')]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

from strategies.funnel_strategy.funnel_config import DEFAULT_FUNNEL_CONFIG
from strategies.funnel_strategy.funnel_engine import FunnelEngine, run_funnel_strategy
from strategies.funnel_strategy.daily_review import DailyReviewer

BEIJING_TZ = timezone(timedelta(hours=8))


def main():
    parser = argparse.ArgumentParser(
        description="七步漏斗选股策略 v1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
七步闭环：
  Layer 0: 大盘风控 — 上涨家数 + 全A指数>20EMA
  Layer 1: 硬性防雷 — ST/财务质量/营收
  Layer 2: 流动性筛选 — 成交额/市值/换手
  Layer 3: 趋势结构过滤 — 周线/EMA排列/年线
  Layer 4: 动能与买入信号 — K线形态/VWAP/量比/乖离
  Layer 5: 人气精选 — 综合评分+人气榜
  Layer 6: 刚性风控 — ATR止损/时段/盈亏比

核心纪律：每晚复盘，连续3次止损失败暂停交易一天。
        """
    )

    parser.add_argument("--date", "-d", type=str, default=None,
                        help="交易日期 (YYYY-MM-DD)，默认最新交易日")
    parser.add_argument("--output", "-o", type=str, default="./results",
                        help="输出目录")
    parser.add_argument("--disable", nargs="+", type=int, default=[], action="append",
                        help="禁用的层级 (0-6)，可重复指定")
    parser.add_argument("--funnel", action="store_true", default=True,
                        help="运行漏斗选股 (默认)")
    parser.add_argument("--review", action="store_true",
                        help="复盘昨日推荐")
    parser.add_argument("--full-cycle", action="store_true",
                        help="完整闭环：复盘+选股")
    args = parser.parse_args()

    if args.full_cycle:
        args.review = True
        args.funnel = True

    reviewer = DailyReviewer()

    # Step A: 复盘昨日
    if args.review:
        result = reviewer.review_yesterday()
        summary = reviewer.get_summary()

    # Step B: 运行漏斗选股
    if args.funnel:
        cfg = DEFAULT_FUNNEL_CONFIG
        cfg.output_dir = args.output
        disabled_layers = []
        for group in (args.disable or []):
            disabled_layers.extend(group)
        for layer_num in disabled_layers:
            if 0 <= layer_num <= 6:
                setattr(cfg, f'layer{layer_num}_enabled', False)

        # 纪律检查：连续止损失败暂停交易 [④纪律]
        if cfg.discipline_review_check_all:
            can_trade, reason = reviewer.is_trading_allowed()
            if not can_trade:
                print(f"\n{'='*70}")
                print(f"  🚫 {reason}")
                print(f"  → 跳过当日选股")
                print(f"{'='*70}\n")
                sys.exit(0)

        trade_date = None
        if args.date:
            trade_date = date.fromisoformat(args.date)

        result = run_funnel_strategy(trade_date=trade_date, cfg=cfg)
        success = len(result.get('candidates', [])) > 0
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
