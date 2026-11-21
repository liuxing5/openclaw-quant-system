"""Fetch a slice of daily-pick 5m bars (timeout-guarded by caller)."""
import os
import pickle
import sys

import pandas as pd

CACHE = "data/research/morning_exit_bars.pkl"


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


def main():
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    picks = pd.read_parquet("data/research/daily_picks.parquet")
    picks = picks[(picks["rank"] == 1) & (picks.trade_date >= "2025-01-01")].reset_index(drop=True)
    cache = pickle.loads(open(CACHE, "rb").read()) if os.path.exists(CACHE) else {}
    todo = []
    for r in picks.iloc[lo:hi].itertuples():
        nxt = str(r.next_date)[:10]
        key = (r.ts_code, str(r.trade_date.date()), nxt)
        if key not in cache or not cache[key] or "volume" not in cache[key][0]:
            todo.append((r.ts_code, str(r.trade_date.date()), nxt))
    if not todo:
        print("nothing to fetch")
        return
    import baostock as bs
    if bs.login().error_code != "0":
        print("login failed")
        return
    try:
        for code, s, e in todo:
            try:
                cache[(code, s, e)] = fetch_5m(bs_code(code), s, e)
            except Exception as exc:
                print("fail", code, s, exc, flush=True)
                cache[(code, s, e)] = []
            pickle.dump(cache, open(CACHE, "wb"))
        print("fetched", len(todo))
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
