"""
主升浪检测策略 — 回测执行入口
=================================
用法:
  # 全市场回测
  python -m strategies.main_uptrend.run_backtest --start 2025-01-01 --end 2026-05-15

  # 单票深度分析 (603115 海星股份)
  python -m strategies.main_uptrend.run_backtest --symbol sh.603115 --analyze

  # 指定标的列表回测
  python -m strategies.main_uptrend.run_backtest --symbols sh.603115,sz.000001 --start 2025-01-01

  # 跳过 A 层预筛（全市场直接扫）
  python -m strategies.main_uptrend.run_backtest --no-a --start 2025-01-01
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import os
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from strategies.main_uptrend.config import MainUptrendConfig, DEFAULT_CONFIG
from strategies.main_uptrend.backtester import MainUptrendBacktester

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def _build_json_summary(result: dict, start: str, end: str) -> dict:
    summary = {
        'backtest_start': start,
        'backtest_end': end,
        'total_signals': len(result.get('signals', [])),
        'forward_returns': {},
        'top10_signals': [],
    }
    for days, df in result.get('forward_returns', {}).items():
        if df.empty:
            continue
        rets = df[f'ret_{days}d'].dropna()
        if len(rets) == 0:
            continue
        arr = rets.values
        summary['forward_returns'][str(days)] = {
            'count': len(arr),
            'win_rate': float(np.mean(arr > 0)),
            'hit_rate_5pct': float(np.mean(arr > 0.05)),
            'mean': float(np.mean(arr)),
            'median': float(np.median(arr)),
            'max': float(np.max(arr)),
            'min': float(np.min(arr)),
            'std': float(np.std(arr)),
            'above_3pct': float(np.mean(arr > 0.03)),
            'above_5pct': float(np.mean(arr > 0.05)),
            'above_10pct': float(np.mean(arr > 0.10)),
        }
    sorted_signals = sorted(
        result.get('signals', []),
        key=lambda x: x.get('composite_score', 0),
        reverse=True,
    )
    for s in sorted_signals[:10]:
        top = {
            'ts_code': s.get('ts_code', ''),
            'eval_date': s.get('eval_date', ''),
            'composite_score': round(s.get('composite_score', 0), 1),
            'b_score': round(s.get('b_score', 0), 1),
            'c_score': round(s.get('c_score', 0), 1),
            'entry_price': s.get('entry_price'),
            'forward_rets': {},
        }
        for d in [10, 20, 60]:
            key = f'ret_{d}d'
            if key in s and s[key] is not None:
                top['forward_rets'][str(d)] = round(float(s[key]), 4)
        summary['top10_signals'].append(top)
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="主升浪检测策略 — 回测执行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 全市场回测
  python -m strategies.main_uptrend.run_backtest --start 2025-01-01 --end 2026-05-15

  # 603115 海星股份 单票深度分析
  python -m strategies.main_uptrend.run_backtest --symbol sh.603115 --analyze

  # 多只标的聚焦回测
  python -m strategies.main_uptrend.run_backtest --symbols sh.603115 --start 2025-02-01

  # 跳过 A 层预筛
  python -m strategies.main_uptrend.run_backtest --no-a --start 2025-02-01 --end 2025-05-15
        """
    )
    parser.add_argument("--start", type=str, default="2025-01-01",
                        help="回测起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2026-05-15",
                        help="回测结束日期 (YYYY-MM-DD)")
    parser.add_argument("--symbol", type=str, default=None,
                        help="单只标的 (如 sh.603115)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="多只标的，逗号分隔 (如 sh.603115,sz.000001)")
    parser.add_argument("--analyze", action="store_true",
                        help="单票深度分析模式")
    parser.add_argument("--no-a", action="store_true",
                        help="跳过 A 层预筛（直接全市场扫，适合回测验证 B/C/D）")
    parser.add_argument("--no-c", action="store_true",
                        help="跳过 C 层持续性判定")
    parser.add_argument("--no-d", action="store_true",
                        help="跳过 D 层风险过滤")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出文件路径（.txt）")
    parser.add_argument("--top-b", type=int, default=20,
                        help="B 层 Top N 数量")
    parser.add_argument("--top-c", type=int, default=8,
                        help="C 层 Top N 数量")

    args = parser.parse_args()

    cfg = MainUptrendConfig(
        backtest_start=args.start,
        backtest_end=args.end,
        a_enabled=not args.no_a,
        c_enabled=not args.no_c,
        d_enabled=not args.no_d,
        b_top_n_daily=args.top_b,
        c_top_n_daily=args.top_c,
    )

    bt = MainUptrendBacktester(cfg)

    # ---- 单票深度分析 ----
    if args.analyze and args.symbol:
        result = bt.analyze_single_stock(
            args.symbol,
            start_date=args.start,
            end_date=args.end,
        )
        print("\n" + "=" * 60)
        print(f"单票深度分析: {args.symbol}")
        print("=" * 60)
        print(result.get('summary', '无结果'))
        print(f"\n检测详情: {len(result.get('detections', []))} 次")

        if args.output:
            out_path = args.output
        else:
            out_dir = Path("strategies/main_uptrend/results")
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"analyze_{args.symbol.replace('.','_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(result.get('summary', ''))
        print(f"\n结果已保存: {out_path}")
        return

    # ---- 多只标的回测 ----
    target_stocks = None
    if args.symbol:
        target_stocks = [args.symbol]
    elif args.symbols:
        target_stocks = [s.strip() for s in args.symbols.split(",") if s.strip()]

    print(f"\n{'=' * 60}")
    print(f"主升浪检测策略 — 回测")
    print(f"{'=' * 60}")
    print(f"回测区间: {args.start} ~ {args.end}")
    print(f"A 层预筛: {'启用' if cfg.a_enabled else '跳过'}")
    print(f"C 层持续性: {'启用' if cfg.c_enabled else '跳过'}")
    print(f"D 层风险: {'启用' if cfg.d_enabled else '跳过'}")
    if target_stocks:
        print(f"目标标的: {len(target_stocks)} 只")
    else:
        print(f"范围: 全市场")
    print(f"{'=' * 60}\n")

    result = bt.run(
        start_date=args.start,
        end_date=args.end,
        target_stocks=target_stocks,
    )

    if result:
        print(result.get('summary', '无结果'))

        if args.output:
            out_path = args.output
        else:
            out_dir = Path("strategies/main_uptrend/results")
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            out_path = out_dir / f"backtest_{args.start}_{args.end}_{ts}.txt"

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(result.get('summary', ''))
        print(f"\n结果已保存: {out_path}")

        if result.get('forward_returns'):
            for days, df in result['forward_returns'].items():
                if not df.empty:
                    csv_path = str(out_path).replace('.txt', f'_fwd{days}d.csv')
                    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
                    print(f"前向收益明细已保存: {csv_path}")

        json_result = _build_json_summary(result, args.start, args.end)
        docs_dir = Path("strategies/funnel_strategy/docs")
        docs_dir.mkdir(parents=True, exist_ok=True)
        json_path = docs_dir / "main_uptrend_backtest.json"
        json_path.write_text(
            json.dumps(json_result, ensure_ascii=False, indent=2, default=str),
            encoding='utf-8',
        )
        print(f"JSON 已保存: {json_path}")


if __name__ == "__main__":
    main()