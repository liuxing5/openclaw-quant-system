# -*- coding: utf-8 -*-
"""导出最严格杨永兴版回测的逐笔/逐日 CSV，并追加逐日推荐到报告。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports"
data = json.loads((REPORT / "yang_strict_backtest.json").read_text(encoding="utf-8"))
rows = data["rows"]
daily = data["daily"]

with (REPORT / "yang_strict_trades.csv").open("w", encoding="utf-8-sig", newline="") as f:
    f.write(
        "signal_date,next_trade_date,ts_code,stock_name,confirmed,entry_price,vwap,"
        "max_return,close_0940,net_ret_0940,hit_1,hit_15,hit_2,hit_25,hit_3\n"
    )
    for r in rows:
        f.write(
            f"{r['signal_date']},{r.get('next_trade_date') or ''},{r['ts_code']},{r.get('stock_name') or ''},"
            f"{r.get('confirmed')},{r.get('entry_price') or ''},{r.get('vwap') or ''},"
            f"{r.get('max_return') if r.get('max_return') is not None else ''},"
            f"{r.get('close_1030') or ''},{r.get('net_ret_1030') if r.get('net_ret_1030') is not None else ''},"
            f"{r.get('hit_1')},{r.get('hit_15')},{r.get('hit_2')},{r.get('hit_25')},{r.get('hit_3')}\n"
        )

by_day = {}
for r in rows:
    by_day.setdefault(r["signal_date"], []).append(r)
with (REPORT / "yang_strict_daily.csv").open("w", encoding="utf-8-sig", newline="") as f:
    f.write("trade_date,daily_candidates,confirmed,next_trade_date,confirmed_codes,confirmed_names\n")
    for d in daily:
        items = [r for r in by_day.get(d["trade_date"], []) if r.get("confirmed")]
        f.write(
            f"{d['trade_date']},{d['daily_candidates']},{d['confirmed']},{d.get('next_trade_date') or ''},"
            f"{'|'.join(r['ts_code'] for r in items)},{'|'.join((r.get('stock_name') or '') for r in items)}\n"
        )

md_path = REPORT / "yang_strict_backtest.md"
md = md_path.read_text(encoding="utf-8")
section = ["## 逐日推荐明细（仅确认信号）", "", "| 日期 | 次日 | 推荐代码 | 名称 | 当日候选数 |", "|---|---|---|---|---:|"]
for d in daily:
    items = [r for r in by_day.get(d["trade_date"], []) if r.get("confirmed")]
    if not items:
        continue
    section.append(
        f"| {d['trade_date']} | {d.get('next_trade_date') or '—'} | "
        f"{'、'.join(r['ts_code'] for r in items)} | {'、'.join((r.get('stock_name') or '') for r in items)} | "
        f"{d['daily_candidates']} |"
    )
section.append("")
section.append("完整逐笔和 10 分钟 K 线见：`yang_strict_trades.csv`、`yang_strict_10m.csv`。")
section.append("")
section.append("## 数据口径限制")
section.append("")
section.append("- 免费历史接口没有 2026-04~10 的 1 分钟线；本报告用 baostock **5 分钟线聚合的 10 分钟线**，只能近似 9:30-9:40。")
section.append("- 若需要真正的 1 分钟线，需要提供 Tushare/Ricequant/聚宽等有分钟权限的数据源。")
section.append("")
if "## 逐日推荐明细（仅确认信号）" in md:
    before, rest = md.split("## 说明", 1)
    before = before.split("## 逐日推荐明细（仅确认信号）")[0]
    md = before + "\n".join(section) + "\n## 说明" + rest
else:
    md = md.replace("## 说明", "\n".join(section) + "\n## 说明")
md_path.write_text(md, encoding="utf-8")
print("exported", len(rows), "trades", len(daily), "days")
