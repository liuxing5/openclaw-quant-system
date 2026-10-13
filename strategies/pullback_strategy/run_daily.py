"""回调买入法 — 每日选股入口

Usage:
  python -m strategies.pullback_strategy.run_daily            # 最新交易日
  python -m strategies.pullback_strategy.run_daily --date 2026-10-12

输出:
  daily_candidates (source='pullback_buy', run_mode='afternoon')
  strategy_scans   (strategy='pullback_buy')  —— 供 funnel 报告页渲染四步漏斗
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from dotenv import load_dotenv
for _env_path in [Path('.env'), Path('strategies/llm_multisource/.env')]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

from core.db.connection import get_db_fresh
from core.db.candidates import write_candidates
from strategies.pullback_strategy.config import DEFAULT_CONFIG as CFG
from strategies.pullback_strategy import engine

BEIJING_TZ = timezone(timedelta(hours=8))


def _close(conn):
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def latest_trade_date() -> str:
    conn = get_db_fresh()
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM daily_quotes;")
        d = cur.fetchone()[0]
        cur.close()
        return str(d)
    finally:
        _close(conn)


def persist_scan_stats(scan_date, counts: dict, total_scanned: int, total_passed: int):
    conn = get_db_fresh()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO strategy_scans
                (snapshot_date, strategy, total_scanned, total_passed, filter_stats)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (snapshot_date, strategy) DO UPDATE SET
                total_scanned = EXCLUDED.total_scanned,
                total_passed  = EXCLUDED.total_passed,
                filter_stats  = EXCLUDED.filter_stats;
        """, (scan_date, 'pullback_buy', total_scanned, total_passed,
              json.dumps(counts, ensure_ascii=False)))
        conn.commit()
        cur.close()
        print(f"  ✓ 扫描统计已写入 strategy_scans ({scan_date})")
    finally:
        _close(conn)


def run(date: str | None = None):
    scan_date = date or latest_trade_date()
    # EMA50 + 20日动能需要预热期：往前多取 120 个自然日
    start = str(datetime.strptime(scan_date, "%Y-%m-%d").date() - timedelta(days=260))
    print(f"[回调买入法] 扫描日期={scan_date}  数据窗口={start} ~ {scan_date}")

    meta = engine.load_stock_meta()
    df = engine.load_quotes(start, scan_date)
    if df.empty:
        print("❌ 无行情数据")
        return 1
    df = engine.add_indicators(df, CFG)

    counts = engine.step_counts(df, CFG, meta, scan_date)
    if not counts:
        print(f"⚠️ {scan_date} 无行情数据（daily_quotes 未就绪）")
        return 1

    sigs = engine.generate_signals(df, CFG, meta, end=scan_date)
    # generate_signals 已按 end 过滤，再精确到扫描日
    sigs = sigs[sigs["trade_date"] == pd.Timestamp(scan_date)]
    sigs = sigs.sort_values("final_score", ascending=False).head(CFG.top_n)

    total_scanned = counts.get("流动性/风控", 0)
    total_passed = counts.get("波动率止损达标", 0)
    persist_scan_stats(scan_date, counts, total_scanned, total_passed)

    items = []
    for _, r in sigs.iterrows():
        entry = float(r["close"])
        stop = float(r["stop_loss"])
        target = float(r["target_1"])
        items.append({
            'ts_code': r['ts_code'],
            'stock_name': r.get('stock_name') or '',
            'final_score': float(r['final_score']),
            'quant_score': float(r['quant_score']),
            'selected': True,
            'entry_low': round(entry, 3),
            'entry_high': round(entry * 1.01, 3),
            'stop_loss': round(stop, 3),
            'target_1': round(target, 3),
            'target_2': round(entry + CFG.reward_risk * 1.5 * (entry - stop), 3),
            'logic_tags': [
                f"动能分位{r['mom_pctile'] * 100:.0f}%",
                f"回撤{(1 - r['low5'] / r['hi20_prev']) * 100:.0f}%",
                f"止损{r['risk_pct'] * 100:.1f}%",
                f"盈亏比{CFG.reward_risk:.0f}:1",
            ],
            'sources': [{'source': 'pullback_buy', 'score': float(r['final_score'])}],
        })

    n = write_candidates(items, snapshot_date=scan_date, source='pullback_buy',
                         run_mode='afternoon')
    print(f"  ✓ 候选写入 daily_candidates: {n} 只 (source=pullback_buy)")
    if items:
        for it in items:
            print(f"    {it['ts_code']} {it['stock_name']:<6} 分={it['final_score']:.0f} "
                  f"入场={it['entry_low']} 止损={it['stop_loss']} 目标={it['target_1']}")
    else:
        print("    （今日无满足四步条件的标的 —— 空仓也是结果）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="回调买入法 — 每日选股")
    ap.add_argument("--date", type=str, default=None, help="扫描日期 YYYY-MM-DD，默认最新")
    args = ap.parse_args()
    return run(args.date)


if __name__ == "__main__":
    sys.exit(main())
