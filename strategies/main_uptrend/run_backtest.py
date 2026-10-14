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
from typing import Dict

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
    signals = result.get('signals', [])
    summary = {
        'backtest_start': start,
        'backtest_end': end,
        'total_signals': len(signals),
        # 审查 30：策略标识 + 漏斗计数（候选→确认）
        'strategy': 'main_uptrend',
        'strategy_name': '主升浪检测策略 (B启动+C持续+D风险+E趋势)',
        'funnel': result.get('funnel') or {},
        # 2026-10-13 参数审查：成本与显著性口径说明（新增字段，旧字段全保留）
        'cost_note': COST_NOTE,
        'round_trip_cost': ROUND_TRIP_COST,
        'significance_note': SIGNIFICANCE_NOTE,
        'forward_returns': {},
        'benchmark': result.get('benchmark') or {},
        'excess': {},
        'exit_stats': {},
        'by_year': {},
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

        # 审查 30：与全市场等权基准的超额（同窗口同口径）
        bm = (result.get('benchmark') or {}).get(str(days)) or {}
        if bm.get('mean') is not None:
            stats['benchmark_mean'] = float(bm['mean'])
            stats['benchmark_win_rate'] = float(bm.get('win_rate', 0.0))
            stats['benchmark_count'] = int(bm.get('count', 0))
            summary['excess'][str(days)] = float(stats['mean']) - float(bm['mean'])

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

    # 审查 30：exit_stats（趋势跟踪退出口径，页面渲染出场分布/胜率/持有天数）
    exit_rets = [s['exit_ret'] for s in signals
                 if s.get('exit_ret') is not None and np.isfinite(s['exit_ret'])]
    if exit_rets:
        er = np.array(exit_rets, dtype=float)
        exits: Dict[str, int] = {}
        hold_days = []
        truncated = 0
        for s in signals:
            if s.get('exit_ret') is None or not np.isfinite(s['exit_ret']):
                continue
            et = s.get('exit_type') or 'unknown'
            exits[et] = exits.get(et, 0) + 1
            if s.get('exit_day') is not None:
                hold_days.append(float(s['exit_day']))
            if s.get('exit_truncated'):
                truncated += 1
        eval_dates_all = [str(s.get('eval_date', ''))[:10] for s in signals
                          if s.get('exit_ret') is not None and np.isfinite(s['exit_ret'])]
        exit_sig = fwd_stats(er, dates=eval_dates_all, horizon=1,
                             trading_days=trading_days)
        summary['exit_stats'] = {
            'win_rate': float(np.mean(er > 0)),
            'mean': float(np.mean(er)),
            'median': float(np.median(er)),
            'max': float(np.max(er)),
            'min': float(np.min(er)),
            't': exit_sig.get('t', 0.0),
            'p': exit_sig.get('p', 1.0),
            'n_eff': exit_sig.get('n_eff', 0),
            'low_power': bool(exit_sig.get('low_power', True)),
            'avg_hold_days': float(np.mean(hold_days)) if hold_days else 0.0,
            'stop_count': int(exits.get('stop_loss', 0)),
            'target_count': int(exits.get('trailing_stop', 0)),
            'timeout_count': int(exits.get('max_hold', 0)),
            'ma10_count': int(exits.get('ma10_exit', 0)),
            'ma20_count': int(exits.get('ma20_exit', 0)),
            'window_end_count': int(exits.get('window_end', 0)),
            'exit_truncated_count': truncated,
            'exit_type_counts': exits,
            'sig_source': 'fwd_stats_by_eval_date',
        }

    # 审查 30：顶层 by_year（出场收益 + 前向10日，页面"分子期稳定性"表消费）
    yearly: Dict[str, Dict] = {}
    for s in signals:
        yr = str(s.get('eval_date', ''))[:4]
        if not yr.isdigit():
            continue
        e = yearly.setdefault(yr, {'rets': [], 'f10': []})
        if s.get('exit_ret') is not None and np.isfinite(s['exit_ret']):
            e['rets'].append(float(s['exit_ret']))
        if s.get('ret_10d') is not None:
            e['f10'].append(float(s['ret_10d']))
    for yr, e in sorted(yearly.items()):
        yr_entry: Dict = {'count': len(e['rets']) + len(e['f10'])}
        # 问题19 同口径：分年样本 <30 不报比率（噪声）
        if len(e['rets']) >= 30:
            r = np.array(e['rets'])
            yr_entry['win_rate'] = float(np.mean(r > 0))
            yr_entry['mean'] = float(np.mean(r))
        else:
            yr_entry['low_sample'] = True
        if len(e['f10']) >= 30:
            f = np.array(e['f10'])
            yr_entry['fwd10_mean'] = float(np.mean(f))
            yr_entry['fwd10_win'] = float(np.mean(f > 0))
        summary['by_year'][yr] = yr_entry

    sorted_signals = sorted(
        signals,
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
            'confirmed': bool(s.get('confirmed', False)),
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
            # 审查 5：未确认信号保留记录（confirmed=False, ret*=None）
            'confirmed': bool(s.get('confirmed', False)),
            'confirm_reject': s.get('confirm_reject'),
            'exit_ret': round(float(s['exit_ret']), 4) if s.get('exit_ret') is not None else None,
            'max_ret': round(float(s['max_ret']), 4) if s.get('max_ret') is not None else None,
            'exit_type': s.get('exit_type', ''),
            'exit_day': s.get('exit_day'),
            'exit_truncated': bool(s.get('exit_truncated', False)),
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
    parser.add_argument("--top-b", type=int, default=None,
                        help="B 层 Top N 数量（默认用 config.b_top_n_daily，仅显式指定时覆盖）")
    parser.add_argument("--top-c", type=int, default=None,
                        help="C 层 Top N 数量（默认用 config.c_top_n_daily，仅显式指定时覆盖）")

    args = parser.parse_args()

    # 审查 9：CLI 默认 None，只有显式指定才覆盖 config 默认值
    # （旧版 CLI default=5 会把 config 里调好的值悄悄覆盖掉）
    cfg = MainUptrendConfig(
        backtest_start=args.start,
        backtest_end=args.end,
        a_enabled=not args.no_a,
        c_enabled=not args.no_c,
        d_enabled=not args.no_d,
        b_top_n_daily=args.top_b if args.top_b is not None
        else DEFAULT_CONFIG.b_top_n_daily,
        c_top_n_daily=args.top_c if args.top_c is not None
        else DEFAULT_CONFIG.c_top_n_daily,
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
        # 审查 18：双份输出——本策略 results/ 留档 + funnel 页面 docs/ 消费
        json_text = json.dumps(json_result, ensure_ascii=False, indent=2, default=str)
        results_dir = Path("strategies/main_uptrend/results")
        results_dir.mkdir(parents=True, exist_ok=True)
        json_paths = [
            results_dir / "main_uptrend_backtest.json",
            Path("strategies/funnel_strategy/docs/main_uptrend_backtest.json"),
        ]
        for jp in json_paths:
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(json_text, encoding='utf-8')
            print(f"JSON 已保存: {jp}")


if __name__ == "__main__":
    main()