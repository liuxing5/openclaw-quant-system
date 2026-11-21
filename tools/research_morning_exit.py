"""灏剧洏涔板叆 / 娆℃棩 09:30-10:00 鍗栧嚭 鈥斺€?鐢?baostock 5 鍒嗛挓绾跨簿纭洖娴嬨€?
閫夎偂锛歍 鏃ユ敹鐩樻潯浠讹紙14:30 鎵弿銆?4:50 涔板叆鐨勮繎浼硷級銆?鍗栧嚭锛歍+1 09:40 / 09:50 / 10:00 鏀剁洏锛屽彲閫?-2% 鐩樹腑姝㈡崯銆?"""
from __future__ import annotations

import json
import os
import pickle
import random
import time

import numpy as np
import pandas as pd

from strategy_lab import load, universe

CACHE = "data/research/morning_exit_bars.pkl"
N_PER_RULE = int(os.environ.get("N_PER_RULE", "200"))


def candidates(df):
    u = universe(df)
    base = (df.amount >= 1e8) & df.circ_mcap_yi.between(50, 2000) & u
    rules = {
        "panic": base & df.pct_chg.between(-6, -1.5) & (df.close_pos < 0.40)
                 & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > 0.03)
                 & df.turnover_rate.between(1.5, 8),
        "deep_panic": base & df.pct_chg.between(-9, -4) & (df.close_pos < 0.30),
        "old_8step": base & df.pct_chg.between(3, 9.7) & df.turnover_rate.between(5, 10)
                     & (df.close > df.ma5) & (df.ma5 > df.ma10),
        "strong_close": base & df.pct_chg.between(3, 7) & (df.close_pos > 0.80),
        "limit_up": base & (df.pct_chg >= 9.5),
    }
    out = []
    for name, m in rules.items():
        d = df[m & (df.trade_date >= "2025-01-01") & (df.trade_date <= "2026-04-21")]
        d = d.dropna(subset=["f_date1"])
        if len(d) > N_PER_RULE:
            d = d.sample(N_PER_RULE, random_state=42)
        for _, r in d.iterrows():
            out.append({"rule": name, "ts_code": r["ts_code"],
                        "signal_date": str(r["trade_date"].date()),
                        "next_date": str(pd.Timestamp(r["f_date1"]).date())})
    return out


def bs_code(ts_code: str) -> str:
    return ("sh." if ts_code.endswith(".SH") else "sz.") + ts_code.split(".")[0]


def fetch_5m(code: str, start: str, end: str) -> list[dict]:
    import baostock as bs
    rs = bs.query_history_k_data_plus(
        code, "date,time,open,high,low,close,volume,amount",
        start_date=start, end_date=end, frequency="5", adjustflag="2")
    out = []
    while rs.error_code == "0" and rs.next():
        r = rs.get_row_data()
        try:
            out.append({"date": r[0], "time": r[1][8:12],
                        "open": float(r[2]), "high": float(r[3]),
                        "low": float(r[4]), "close": float(r[5])})
        except (TypeError, ValueError):
            continue
    return out


def evaluate(bars, next_date: str, stops=(None, -0.02, -0.03)):
    t_bars = [b for b in bars if b["date"] < next_date]
    n_bars = [b for b in bars if b["date"] == next_date]
    if not t_bars or not n_bars:
        return None
    entry = t_bars[-1]["close"]
    res = {"entry": entry}
    for hhmm in ("0935", "0940", "0950", "1000", "1010"):
        bar = next((b for b in n_bars if b["time"] == hhmm), None)
        res[hhmm] = bar["close"] / entry - 1 if bar else None
    res["open"] = n_bars[0]["open"] / entry - 1
    # stops: check 5m lows before 10:00
    for s in stops:
        if s is None:
            continue
        stop_px = entry * (1 + s)
        ret = res.get("1000")
        for b in n_bars:
            if b["time"] > "1000":
                break
            if b["open"] <= stop_px:
                ret = b["open"] / entry - 1
                break
            if b["low"] <= stop_px:
                ret = s
                break
        res[f"stop{s}"] = ret
    return res


def main():
    df = load()
    cands = candidates(df)
    print("signals", len(cands), pd.Series([c["rule"] for c in cands]).value_counts().to_dict())

    cache = pickle.loads(open(CACHE, "rb").read()) if os.path.exists(CACHE) else {}
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise SystemExit(f"login failed: {lg.error_msg}")
    try:
        for i, c in enumerate(cands):
            key = (c["ts_code"], c["signal_date"], c["next_date"])
            if key in cache:
                continue
            bars = fetch_5m(bs_code(c["ts_code"]), c["signal_date"], c["next_date"])
            cache[key] = bars
            if i % 50 == 0:
                print(f"  {i}/{len(cands)} fetched", flush=True)
                pickle.dump(cache, open(CACHE, "wb"))
    finally:
        bs.logout()
        pickle.dump(cache, open(CACHE, "wb"))
    print("cache", len(cache))

    rows = []
    for c in cands:
        bars = cache.get((c["ts_code"], c["signal_date"], c["next_date"]), [])
        r = evaluate(bars, c["next_date"])
        if r:
            rows.append({**c, **r})
    out = pd.DataFrame(rows)
    print("\nvalid", len(out))
    for rule, g in out.groupby("rule"):
        print(f"\n== {rule} n={len(g)} ==")
        for col in ["open", "0935", "0940", "0950", "1000", "stop-0.02", "stop-0.03"]:
            if col not in g:
                continue
            x = g[col].dropna()
            if len(x) < 5:
                continue
            print(f"  {col:10s} mean={x.mean()*100:+.3f}% median={x.median()*100:+.3f}% "
                  f"win={(x>0).mean()*100:.1f}% t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}")
    out.to_csv("data/research/morning_exit.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()

