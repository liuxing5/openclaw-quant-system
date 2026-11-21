"""14:30 扫描 / 14:50 买入 / 次日 09:30-10:00 卖出 —— 用 5 分钟线还原真实时点。

要点：
* 选股特征用 T 日 14:30 之前的数据（价格、日内高低、成交量占比），不用收盘价。
* 买入价用 T 日 14:50 的 5 分钟收盘。
* 卖出用 T+1 09:35/09:40/09:50/10:00 的 5 分钟收盘，可选 -2% 盘中止损。
"""
from __future__ import annotations

import os
import socket
socket.setdefaulttimeout(15)
import pickle
import random

import numpy as np
import pandas as pd

from strategy_lab import load, universe

CACHE = "data/research/morning_exit_bars.pkl"
N = int(os.environ.get("N", "800"))


def candidates(df):
    u = universe(df)
    base = (df.amount >= 1e8) & df.circ_mcap_yi.between(50, 2000) & u
    # 先用日线做一个较宽的候选池（收盘跌幅 -8%~0），14:30 特征再严格过滤
    m = (base & df.pct_chg.between(-8, 0) & (df.close > df.ma20) & (df.ma20 > df.ma60)
         & (df.ret20 > 0.03) & df.turnover_rate.between(1.5, 10)
         & (df.trade_date >= "2025-01-01") & (df.trade_date <= "2026-04-21"))
    d = df[m].dropna(subset=["f_date1"])
    if len(d) > N:
        d = d.sample(N, random_state=7)
    return d


def bs_code(ts_code: str) -> str:
    return ("sh." if ts_code.endswith(".SH") else "sz.") + ts_code.split(".")[0]


def fetch_5m(code, start, end):
    import baostock as bs
    rs = bs.query_history_k_data_plus(code, "date,time,open,high,low,close,volume,amount",
                                     start_date=start, end_date=end, frequency="5", adjustflag="2")
    out = []
    while rs.error_code == "0" and rs.next():
        r = rs.get_row_data()
        try:
            out.append({"date": r[0], "time": r[1][8:12],
                        "open": float(r[2]), "high": float(r[3]), "low": float(r[4]),
                        "close": float(r[5]), "volume": float(r[6]), "amount": float(r[7])})
        except (TypeError, ValueError):
            continue
    return out


def fetch(df):
    cache = pickle.loads(open(CACHE, "rb").read()) if os.path.exists(CACHE) else {}
    import baostock as bs
    bs.login()
    try:
        todo = [r for _, r in df.iterrows()
                if (r.ts_code, str(r.trade_date.date()), str(pd.Timestamp(r.f_date1).date())) not in cache
                or not cache[(r.ts_code, str(r.trade_date.date()), str(pd.Timestamp(r.f_date1).date()))]
                or "volume" not in cache[(r.ts_code, str(r.trade_date.date()), str(pd.Timestamp(r.f_date1).date()))][0]]
        print("to fetch", len(todo))
        for i, r in enumerate(todo):
            key = (r.ts_code, str(r.trade_date.date()), str(pd.Timestamp(r.f_date1).date()))
            try:
                cache[key] = fetch_5m(bs_code(r.ts_code), key[1], key[2])
            except Exception as exc:
                print("  fetch fail", key, exc, flush=True)
                cache[key] = []
            if i % 100 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
                pickle.dump(cache, open(CACHE, "wb"))
    finally:
        bs.logout()
        pickle.dump(cache, open(CACHE, "wb"))
    return cache


def feature_at(bars, day, hhmm):
    """返回 day 当天截至 hhmm 的 (price, high, low, vol_sofar, vol_day)。"""
    b = [x for x in bars if x["date"] == day]
    if not b:
        return None
    upto = [x for x in b if x["time"] <= hhmm]
    if not upto:
        return None
    return (upto[-1]["close"], max(x["high"] for x in upto), min(x["low"] for x in upto),
            sum(x["volume"] for x in upto), sum(x["volume"] for x in b))


def main():
    df = load()
    cand = candidates(df)
    print("candidate pool", len(cand))
    cache = fetch(cand)

    rows = []
    for r in cand.itertuples():
        sig = str(r.trade_date.date())
        nxt = str(pd.Timestamp(r.f_date1).date())
        bars = cache.get((r.ts_code, sig, nxt), [])
        if not bars:
            continue
        f = feature_at(bars, sig, "1430")
        if f is None:
            continue
        price1430, high, low, vol_s, vol_d = f
        t_bars = [x for x in bars if x["date"] == sig]
        bs_close = t_bars[-1]["close"]
        factor = r.close / bs_close if bs_close else np.nan
        pre_close = r.close / (1 + r.pct_chg / 100) if r.pct_chg is not None else np.nan
        pct1430 = (price1430 * factor / pre_close - 1) * 100 if pre_close and factor == factor else np.nan
        pos1430 = (price1430 - low) / (high - low) if high > low else np.nan
        turn1430 = r.turnover_rate * vol_s / vol_d if vol_d else np.nan

        entry_bar = next((x for x in t_bars if x["time"] == "1450"), t_bars[-1])
        entry = entry_bar["close"]
        n_bars = [x for x in bars if x["date"] == nxt]
        if not n_bars:
            continue
        by = {x["time"]: x for x in n_bars}
        out = {"ts_code": r.ts_code, "signal_date": sig, "next_date": nxt,
               "pct1430": pct1430, "pos1430": pos1430, "turn1430": turn1430,
               "b20": r.b20, "ret20": r.ret20, "amount": r.amount,
               "entry": entry, "gap_open": n_bars[0]["open"] / entry - 1}
        for hh in ("0935", "0940", "0950", "1000"):
            if hh in by:
                out[f"r{hh}"] = by[hh]["close"] / entry - 1
        ret = out.get("r1000")
        for x in n_bars:
            if x["time"] > "1000":
                break
            if x["open"] <= entry * 0.98:
                ret = x["open"] / entry - 1
                break
            if x["low"] <= entry * 0.98:
                ret = -0.02
                break
        out["stop2"] = ret
        rows.append(out)

    d = pd.DataFrame(rows)
    d.to_csv("data/research/1430_signals.csv", index=False, encoding="utf-8-sig")
    print("valid", len(d))

    # 14:30 过滤
    m = (d.pct1430.between(-6, -1.5)) & (d.pos1430 < 0.40) & d.turn1430.between(1.5, 8)
    s = d[m]
    print("\n== 14:30 过滤后 ==")
    print("n", len(s), "days", s.signal_date.nunique())
    for c in ["gap_open", "r0935", "r0940", "r0950", "r1000", "stop2"]:
        x = s[c].dropna()
        if len(x) > 5:
            print(f"  {c:8s} mean={x.mean()*100:+.3f}% median={x.median()*100:+.3f}% "
                  f"win={(x>0).mean()*100:.1f}% t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}")

    # 按 14:30 收盘位置分档
    print("\n== 按 pos1430 分档（r0940）==")
    s2 = s.dropna(subset=["r0940"]).copy()
    s2["posb"] = pd.cut(s2.pos1430, [0, 0.1, 0.2, 0.3, 0.4])
    print(s2.groupby("posb", observed=True).agg(n=("r0940", "size"), mean=("r0940", "mean"),
                                                med=("r0940", "median"),
                                                win=("r0940", lambda x: (x > 0).mean())).round(4).to_string())
    print("\n== 按 pct1430 分档（r0940）==")
    s2["pb"] = pd.cut(s2.pct1430, [-6, -4, -3, -2, -1.5])
    print(s2.groupby("pb", observed=True).agg(n=("r0940", "size"), mean=("r0940", "mean"),
                                              med=("r0940", "median"),
                                              win=("r0940", lambda x: (x > 0).mean())).round(4).to_string())
    print("\n== 按大盘广度 b20 分档（r0940）==")
    s2["bb"] = pd.cut(s2.b20, [0, 0.2, 0.35, 0.5, 0.7, 1.0])
    print(s2.groupby("bb", observed=True).agg(n=("r0940", "size"), mean=("r0940", "mean"),
                                              med=("r0940", "median"),
                                              win=("r0940", lambda x: (x > 0).mean())).round(4).to_string())


if __name__ == "__main__":
    main()
