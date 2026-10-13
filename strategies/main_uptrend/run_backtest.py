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

# 与 pullback/funnel 入口保持一致：加载 .env，否则 POSTGRES_* 为空会退化为 localhost 连接
from dotenv import load_dotenv
for _env_path in [Path('.env'), Path('strategies/llm_multisource/.env')]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

from strategies.main_uptrend.config import MainUptrendConfig, DEFAULT_CONFIG
from strategies.main_uptrend.backtester import MainUptrendBacktester
from strategies.shared.backtest_stats import (
    fwd_stats, COST_NOTE, SIGNIFICANCE_NOTE, ROUND_TRIP_COST,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def _build_json_summary(result: dict, start: str, end: str) -> dict:
    trading_days = result.get('trading_days')
    summary = {
        'backtest_start': start,
        'backtest_end': end,
        'total_signals': len(result.get('signals', [])),
        # 2026-10-13 参数审查：成本与显著性口径说明（新增字段，旧字段全保留）
        'cost_note': COST_NOTE,
        'round_trip_cost': ROUND_TRIP_COST,
        'significance_note': SIGNIFICANCE_NOTE,
        'forward_returns': {},
        'top10_signals': [],
        'all_signals': [],
    }
    for days, df in result.get('forward_returns', {}).items():
        if df.empty:
            continue
        rets = df[f'ret_{days}d'].dropna()
        if len(rets) == 0:
            continue
        # 注意：ret_{days}d 已在 backtester 中扣除 0.25% 往返成本（净口径），
        # fwd_stats 要求输入为净收益，直接传入即可。
        arr = rets.values
        dates = None
        if 'signal_date' in df.columns:
            dates = [str(v)[:10] for v in df.loc[rets.index, 'signal_date'].tolist()]

        stats = fwd_stats(arr, dates=dates, horizon=int(days), trading_days=trading_days)

        # 毛/净对比：证明成本已扣
        gross_col = f'ret_{days}d_gross'
        if gross_col in df.columns:
            gross = df.loc[rets.index, gross_col].dropna()
            if len(gross) == len(arr):
                garr = gross.values
                stats['gross_mean'] = float(np.mean(garr))
                stats['gross_win_rate'] = float((garr > 0).mean())

        # 保留旧字段（页面 generate_html 依赖 count/win_rate/mean/...）
        stats.update({
            'hit_rate_5pct': float(np.mean(arr > 0.05)),
            'max': float(np.max(arr)),
            'min': float(np.min(arr)),
            'above_3pct': float(np.mean(arr > 0.03)),
            'above_5pct': float(np.mean(arr > 0.05)),
            'above_10pct': float(np.mean(arr > 0.10)),
            'cost_note': COST_NOTE,
        })

        # 分子期稳定性：按信号年份分别给 count/win_rate/mean
        by_year = {}
        for dt, r in zip(dates or [], arr):
            yr = str(dt)[:4]
            if yr.isdigit():
                by_year.setdefault(yr, []).append(float(r))
        stats['by_year'] = {
            y: {
                'count': len(v),
                'win_rate': float(np.mean(np.array(v) > 0)),
                'mean': float(np.mean(v)),
            }
            for y, v in sorted(by_year.items())
        }

        summary['forward_returns'][str(days)] = stats
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
    for s in sorted_signals:
        sig = {
            'ts_code': s.get('ts_code', ''),
            'eval_date': s.get('eval_date', ''),
            'composite_score': round(s.get('composite_score', 0), 1),
            'e_score': round(s.get('e_score', 0), 1),
            'b_score': round(s.get('b_score', 0), 1),
            'c_score': round(s.get('c_score', 0), 1),
            'signal_type': s.get('signal_type', ''),
            'entry_price': s.get('entry_price'),
            'exit_ret': round(float(s['exit_ret']), 4) if s.get('exit_ret') is not None else None,
            'max_ret': round(float(s['max_ret']), 4) if s.get('max_ret') is not None else None,
            'exit_type': s.get('exit_type', ''),
            'exit_day': s.get('exit_day'),
            'breadth_ma20': s.get('breadth_ma20'),
            'breadth_ma5': s.get('breadth_ma5'),
            'env_score': s.get('env_score'),
        }
        for d in [10, 20, 60]:
            key = f'ret_{d}d'
            if key in s and s[key] is not None:
                sig[f'ret_{d}d'] = round(float(s[key]), 4)
        summary['all_signals'].append(sig)
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
    parser.add_argument("--top-b", type=int, default=5,
                        help="B 层 Top N 数量")
    parser.add_argument("--top-c", type=int, default=5,
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

        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
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