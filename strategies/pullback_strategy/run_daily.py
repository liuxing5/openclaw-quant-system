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
from core.utils.trading_calendar import is_trading_day
from strategies.pullback_strategy.config import DEFAULT_CONFIG as CFG
from strategies.pullback_strategy import engine

BEIJING_TZ = timezone(timedelta(hours=8))
# 问题11: 盘后运行的最早时间（此前盘中跑批会用未定型的半根 K 线）
EARLIEST_RUN_TIME = (15, 10)


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
    # 问题11: 数据完整性校验——盘中跑批会用未定型的半根 K 线；
    # 自动模式（--date 指定历史日期回填时跳过）要求北京时间 >=15:10，
    # 且今天是交易日时 daily_quotes 必须已就绪到今日。
    if date is None:
        now = datetime.now(BEIJING_TZ)
        cutoff = now.replace(hour=EARLIEST_RUN_TIME[0], minute=EARLIEST_RUN_TIME[1],
                             second=0, microsecond=0)
        if now < cutoff:
            print(f"❌ 拒绝运行: 北京时间 {now:%H:%M} 早于 {EARLIEST_RUN_TIME[0]:02d}:"
                  f"{EARLIEST_RUN_TIME[1]:02d}，盘中 K 线未定型会污染信号，"
                  f"请盘后重跑或用 --date 显式指定历史日期回填")
            return 1
        latest = latest_trade_date()
        try:
            today_is_trading = is_trading_day(now.date())
        except Exception:
            today_is_trading = now.weekday() < 5    # 日历查询失败时退化为仅周末判定
        if today_is_trading and latest != now.date().isoformat():
            print(f"❌ 拒绝运行: 今日 {now.date()} 为交易日，但 daily_quotes 最新"
                  f"数据为 {latest}，行情未就绪（请等待数据同步完成后重跑）")
            return 1
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

    # 问题17: total_scanned 原传"流动性/风控"过滤后数量，语义错（那是扫描后的
    # 结果而非扫描量）→ 改传当日全市场行情行数；过滤后数量改键名 post_base_filter
    counts["post_base_filter"] = counts.get("流动性/风控", 0)
    total_scanned = int((df["trade_date"] == pd.Timestamp(scan_date)).sum())
    total_passed = counts.get("波动率止损达标", 0)
    persist_scan_stats(scan_date, counts, total_scanned, total_passed)

    items = []
    for _, r in sigs.iterrows():
        # 问题7: 入场价口径与回测对齐——回测为 T+1 开盘入场（高开>3% 追高放弃、
        # 低开照常成交、开盘<=止损弃单）。此处为 T 日收盘后跑批，尚不知次日开盘，
        # 用 T 收盘 ±3% 作为"次日开盘可接受区间"：区间内可按开盘价入场，
        # 超上沿（高开>3%）放弃。此前直接用 T 收盘价当入场价与回测口径分叉。
        ref = (float(r["raw_close"]) if "raw_close" in r.index and pd.notna(r["raw_close"])
               else float(r["close"]))
        stop = float(r["stop_loss"])
        target = float(r["target_1"])
        entry_low = ref * (1 - 0.03)
        entry_high = ref * (1 + 0.03)
        items.append({
            'ts_code': r['ts_code'],
            'stock_name': r.get('stock_name') or '',
            'final_score': float(r['final_score']),
            'quant_score': float(r['quant_score']),
            'selected': True,
            'entry_low': round(entry_low, 3),
            'entry_high': round(entry_high, 3),
            'stop_loss': round(stop, 3),
            'target_1': round(target, 3),
            'target_2': round(ref + CFG.reward_risk * 1.5 * (ref - stop), 3),
            'logic_tags': [
                f"动能分位{r['mom_pctile'] * 100:.0f}%",
                f"回撤{(1 - r['low5'] / r['hi20_prev']) * 100:.0f}%",
                f"止损{r['risk_pct'] * 100:.1f}%",
                f"盈亏比{CFG.reward_risk:.0f}:1",
                # 问题7: 明示次日入场规则（与回测口径一致）
                "T+1开盘入场：高开>3%放弃，低开照常，开盘<=止损弃单",
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
