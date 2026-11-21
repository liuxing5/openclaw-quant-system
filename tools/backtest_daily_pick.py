"""每日一笔：用 5m 线复核 daily_picks 的 top1/top3 收益。"""
import json
import os
import pickle
import time

import numpy as np
import pandas as pd

CACHE = "data/research/morning_exit_bars.pkl"
COST = 0.0025

from strategy_lab import load as load_panel


def bs_code(ts):
    return ("sh." if ts.endswith(".SH") else "sz.") + ts.split(".")[0]


def fetch_5m(code, start, end):
    import baostock as bs
    rs = bs.query_history_k_data_plus(code, "date,time,open,high,low,close,volume,amount",
                                     start_date=start, end_date=end, frequency="5", adjustflag="2")
    out = []
    while rs.error_code == "0" and rs.next():
        r = rs.get_row_data()
        try:
            out.append({"date": r[0], "time": r[1][8:12], "open": float(r[2]), "high": float(r[3]),
                        "low": float(r[4]), "close": float(r[5]), "volume": float(r[6]), "amount": float(r[7])})
        except (TypeError, ValueError):
            continue
    return out


def ensure_bars(d, panel):
    cache = pickle.loads(open(CACHE, "rb").read()) if os.path.exists(CACHE) else {}
    if os.environ.get("SKIP_FETCH") == "1":
        return cache
    todo = []
    for r in d.itertuples():
        key = (r.ts_code, str(r.trade_date.date()), str(pd.Timestamp(r.next_date).date()))
        if key not in cache or not cache[key] or "volume" not in cache[key][0]:
            todo.append((r.ts_code, key[1], key[2]))
    print("to fetch", len(todo), flush=True)
    if todo:
        import baostock as bs
        bs.login()
        try:
            for i, (code, s, e) in enumerate(todo):
                try:
                    cache[(code, s, e)] = fetch_5m(bs_code(code), s, e)
                except Exception as exc:
                    print("fail", code, s, exc, flush=True)
                    cache[(code, s, e)] = []
                if i % 25 == 0:
                    print("  ", i, flush=True)
                    pickle.dump(cache, open(CACHE, "wb"))
        finally:
            bs.logout()
            pickle.dump(cache, open(CACHE, "wb"))
    return cache


def simulate(bars, sig, nxt):
    t = [b for b in bars if b["date"] == sig]
    n = [b for b in bars if b["date"] == nxt]
    if not t or not n:
        return None
    entry = next((b for b in t if b["time"] == "1450"), t[-1])["close"]
    gap = n[0]["open"] / entry - 1
    if gap >= 0.005:
        return "09:30", n[0]["open"], entry, "高开落袋", gap
    if gap <= -0.03:
        return "09:30", n[0]["open"], entry, "深水砍", gap
    stop = entry * 0.98
    for b in n:
        if b["time"] > "1000":
            break
        if b["open"] <= stop:
            return b["time"][:2] + ":" + b["time"][2:], b["open"], entry, "止损(跳空)", b["open"] / entry - 1
        if b["low"] <= stop:
            return b["time"][:2] + ":" + b["time"][2:], stop, entry, "−2%止损", -0.02
    last = next((b for b in n if b["time"] == "1000"), n[-1])
    return "10:00", last["close"], entry, "持到10:00", last["close"] / entry - 1


def main():
    picks = pd.read_parquet("data/research/daily_picks.parquet")
    panel = load_panel().set_index(["ts_code", "trade_date"])
    picks["next_date"] = [panel.loc[(r.ts_code, r.trade_date)]["f_date1"] for r in picks.itertuples()]
    d1 = picks[picks["rank"] == 1].copy()
    d1 = d1[d1.trade_date >= "2025-01-01"]
    print("top1 days", len(d1), "A", int((d1.tier == "A").sum()), "B", int((d1.tier == "B").sum()))
    cache = ensure_bars(d1, panel)

    rows = []
    for r in d1.itertuples():
        bars = cache.get((r.ts_code, str(r.trade_date.date()), str(pd.Timestamp(r.next_date).date())), [])
        res = simulate(bars, str(r.trade_date.date()), str(pd.Timestamp(r.next_date).date()))
        if res is None:
            continue
        st, sell, entry, why, gros = res
        rows.append({"date": r.trade_date, "code": r.ts_code, "tier": r.tier, "score": r.score,
                     "entry": round(entry, 2), "sell_time": st, "sell": round(sell, 2),
                     "why": why, "gross": round(gros, 4), "net": round(gros - COST, 4),
                     "pct": r.pct, "pos": r.pos, "lu20": r.lu20, "ret5": r.ret5})
    d = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    print("evaluated", len(d), "days", d.date.nunique())
    if len(d) == 0:
        return
    daily = d.groupby("date").net.mean()
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    years = (daily.index.max() - daily.index.min()).days / 365.25
    print({
        "trades": len(d), "win": round((d.net > 0).mean(), 4), "avg": round(d.net.mean(), 4),
        "total": round(eq.iloc[-1] - 1, 4), "ann": round(eq.iloc[-1] ** (1 / years) - 1, 4),
        "dd": round(dd.min(), 4), "sharpe": round(daily.mean() / daily.std() * np.sqrt(252), 2),
    })
    print("\nby tier:")
    print(d.groupby("tier").net.agg(["size", "mean", lambda x: (x > 0).mean()]).round(4).to_string())
    print("\nby exit:")
    print(d.groupby("why").net.agg(["size", "mean", lambda x: (x > 0).mean()]).round(4).to_string())
    m = (1 + daily).groupby([daily.index.year, daily.index.month]).prod() - 1
    print("\nmonthly:\n", m.round(3).to_string())
    d.to_csv("docs/overnight_v2/daily_pick_trades.csv", index=False, encoding="utf-8-sig")
    print("saved docs/overnight_v2/daily_pick_trades.csv")


if __name__ == "__main__":
    main()
