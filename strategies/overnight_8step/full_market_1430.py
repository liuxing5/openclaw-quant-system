"""八步隔夜法 v3.1 · 14:30 全市场扫描 / 14:40-14:50 买入 / 次日卖出。

在 v3.0（恐慌缺口承接）基础上改成用户要的执行节奏：
  * 全市场 5,200+ 只，腾讯实时快照 + DB T-1 均线/涨停基因，不依赖逐只 baostock；
  * 每日输出 A/B/C 三档：A=可买（20% 仓）、B=小仓（10%）、C=观察不买（0 仓）；
  * 买入窗口 14:40-14:50，次日 09:25 看缺口，10:00 前卖出。

写库：daily_candidates(source='overnight_8step_v31')、strategy_scans(strategy='overnight_8step_v31')
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from strategies.overnight_8step import v2_intraday as base  # noqa: E402

SOURCE = "overnight_8step_v31"
RUN_MODE = "intraday"
CFG: Dict[str, Any] = dict(base.CONFIG)
CFG.update({
    "source": SOURCE,
    "run_mode": RUN_MODE,
    "scan_time": "14:30",
    "entry_window": "14:40-14:50",
    "exit_window": "09:30-10:00",
    "pos_max": 0.25,
    "watchlist_max": 10,
})


def select_grades(feat: pd.DataFrame, regime: Dict[str, Any], cfg: Dict[str, Any]) -> pd.DataFrame:
    f = feat.copy()
    f["b20"] = regime["breadth"]
    f["vwap_pos"] = f["vwap_pos"].where(f["vwap_pos"].between(-0.5, 0.5))
    today = pd.Timestamp(regime["date"])
    f["list_date"] = pd.to_datetime(f.get("list_date"), errors="coerce")
    trend = ((f["price"] > f["ma20"]) & (f["ma20"] > f["ma60"])
             & (f["ret20"] > cfg["ret20_min"]) & (f["ret5"] > cfg["ret5_min"])
             & (f["lu20"] <= cfg["lu20_max"]) & (~f["is_st"].fillna(False)))
    fresh = f["list_date"].isna() | (f["list_date"] <= today - pd.Timedelta(days=cfg["min_list_days"]))
    liq = ((f["amount"] >= cfg["min_amount"]) & f["circ_mcap"].between(cfg["mktcap_lo"], cfg["mktcap_hi"])
           & f["turnover"].between(cfg["turnover_lo"], cfg["turnover_hi"])
           & (f["amount_ratio"] >= cfg["amount_ratio_min"]))
    panic = f["pct"].between(cfg["pullback_lo"], cfg["pullback_hi"]) & (f["close_pos"] < 0.40)
    vwap_ok = f["vwap_pos"] > cfg["vwap_pos_min"]
    pos_ok = f["close_pos"] < cfg["close_pos_max"]

    f["grade"] = ""
    a = trend & fresh & liq & panic & vwap_ok & pos_ok & (f["b20"] <= cfg["breadth_max"])
    b = trend & fresh & liq & panic & (~a) & (f["b20"] <= cfg["breadth_max"])
    c = (trend & fresh & f["pct"].between(-8, -1.5) & (f["close_pos"] < 0.40)
         & (f["price"] > f["ma20"]) & f["vwap_pos"].notna())
    f.loc[a, "grade"] = "A"
    f.loc[b, "grade"] = "B"
    f.loc[c & ~a & ~b, "grade"] = "C"
    out = f[f["grade"] != ""].copy()
    if out.empty:
        return out

    pos_score = (1 - out["close_pos"]).clip(0, 1)
    depth = ((cfg["pullback_hi"] - out["pct"]) /
             (cfg["pullback_hi"] - cfg["pullback_lo"])).clip(0, 1)
    vwap_score = ((out["vwap_pos"] - cfg["vwap_pos_min"]) /
                  (0 - cfg["vwap_pos_min"])).clip(0, 1)
    turn_score = 1 - ((out["turnover"] - cfg["turnover_lo"]) /
                      (cfg["turnover_hi"] - cfg["turnover_lo"])).clip(0, 1)
    out["score"] = (55 + 18 * pos_score + 12 * depth + 8 * vwap_score + 7 * turn_score).round(1)
    out["rank"] = out.groupby("grade")["score"].rank(ascending=False, method="first")
    keep = out[((out.grade == "A") & (out["rank"] <= 6))
               | ((out.grade == "B") & (out["rank"] <= 3))
               | ((out.grade == "C") & (out["rank"] <= 2))]
    return keep.sort_values(["grade", "score"], ascending=[True, False])


def persist(result: Dict[str, Any]) -> int:
    from core.db.candidates import write_candidates
    date_str = result["trade_date"]
    rows = []
    weight = {"A": 0.20, "B": 0.10, "C": 0.0}
    for _, r in result["candidates"].iterrows():
        grade = r["grade"]
        entry = float(r["price"])
        rows.append({
            "ts_code": r["ts_code"], "stock_name": r.get("stock_name"),
            "final_score": float(r["score"]), "quant_score": float(r["score"]),
            "logic_tags": [
                "八步v3.1", f"{grade}级", "14:30全市场", "14:40-14:50买入",
                f"回踩{float(r['pct']):.1f}%", f"位置{float(r['close_pos']):.2f}",
                f"VWAP{float(r['vwap_pos'])*100:+.1f}%", f"涨停基因{int(r['lu20'])}",
                "次日09:25看缺口", "10:00前卖出",
            ],
            "selected": grade in ("A", "B") and not result["blocked"],
            "position_pct": weight[grade] if not result["blocked"] else 0.0,
            "entry_low": round(entry * 0.995, 2), "entry_high": round(entry * 1.005, 2),
            "stop_loss": round(entry * 0.98, 2), "target_1": None, "target_2": None,
            "sources": {"grade": grade, "regime": result["regime"], "steps": result["step_counts"],
                        "exit": "次日09:25 浮盈>=+0.5%或浮亏<=-3%开盘卖；否则-2%止损，10:00前卖"},
        })
    conn = base._db()
    try:
        cur = conn.cursor()
        cur.execute("delete from daily_candidates where snapshot_date=%s and source=%s", (date_str, SOURCE))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    n = write_candidates(rows, date_str, source=SOURCE, run_mode=RUN_MODE)

    stats = dict(result["step_counts"])
    stats["A级"] = int((result["candidates"].grade == "A").sum())
    stats["B级"] = int((result["candidates"].grade == "B").sum())
    stats["C级"] = int((result["candidates"].grade == "C").sum())
    conn = base._db()
    try:
        cur = conn.cursor()
        cur.execute("delete from strategy_scans where snapshot_date=%s and strategy=%s", (date_str, SOURCE))
        cur.execute("""insert into strategy_scans
            (snapshot_date, strategy, total_scanned, total_passed, filter_stats, sentiment_score, mood)
            values (%s,%s,%s,%s,%s,%s,%s)""",
            (date_str, SOURCE, result["universe"], len(rows), json.dumps(stats, ensure_ascii=False),
             round(result["breadth"] * 100, 1), "risk_on" if not result["blocked"] else "risk_off"))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    out_dir = os.path.join(BASE_DIR, "docs", "overnight_v2")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "latest_1430.json"), "w", encoding="utf-8") as f:
        json.dump({"trade_date": date_str, "scan_time": "14:30", "entry_window": "14:40-14:50",
                   "universe": result["universe"], "breadth": result["breadth"],
                   "blocked": result["blocked"], "steps": stats,
                   "candidates": rows}, f, ensure_ascii=False, indent=2)
    return n


def scan_full(trade_date: str) -> Dict[str, Any]:
    daily, index_hist = base.load_daily(trade_date, days=150)
    codes = [("sh" if ts.split(".")[1] == "SH" else "sz") + ts.split(".")[0] for ts in daily["ts_code"]]
    snap = base.fetch_snapshots(codes)
    if len(snap) < 500:
        raise RuntimeError(f"{trade_date} 快照覆盖不足 {len(snap)}")
    feat = base.build_features(daily, snap, CFG)
    breadth = float((feat["price"] > feat["ma20"]).mean())
    regime = base.market_regime(base._index_with_today(index_hist, snap, trade_date), breadth, CFG)
    cand = select_grades(feat, regime, CFG)
    return {"trade_date": trade_date, "universe": len(feat), "breadth": round(breadth, 4),
            "regime": regime, "blocked": bool(regime["blocked"]), "candidates": cand,
            "step_counts": base.step_stats(feat, regime, CFG)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    day = args.date or datetime.now().strftime("%Y-%m-%d")
    result = scan_full(day)
    print(f"\n=== 八步 v3.1 全市场 14:30 · {day} ===")
    print(f"全市场 {result['universe']} · 广度 {result['breadth']:.1%} · 门控 "
          f"{'关闭' if result['blocked'] else '开启'}")
    for _, r in result["candidates"].iterrows():
        print(f"  [{r['grade']}] {r['ts_code']} {r.get('stock_name','')} {r['price']:.2f} "
              f"跌{r['pct']:+.1f}% 位置{r['close_pos']:.2f} VWAP{r['vwap_pos']*100:+.1f}% 评分{r['score']:.0f}")
    if not args.dry_run:
        n = persist(result)
        print(f"✅ 写入 {n} 条（source={SOURCE}）")


if __name__ == "__main__":
    main()
