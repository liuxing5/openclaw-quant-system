"""批量拉 150 只 A 级样本的 5m 线（可分批，超时由外层控制；每只落盘）。"""
import os
import pickle
import sys

import pandas as pd

CACHE = "data/research/morning_exit_bars.pkl"
SAMPLE = "data/research/a_1y_sample.parquet"


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
    s = pd.read_parquet(SAMPLE)
    cache = pickle.loads(open(CACHE, "rb").read()) if os.path.exists(CACHE) else {}
    todo = []
    for r in s.iloc[lo:hi].itertuples():
        key = (r.ts_code, r.sig_date, r.next_date)
        if key not in cache or not cache[key] or "volume" not in cache[key][0]:
            todo.append(key)
    if not todo:
        print("nothing to fetch")
        return
    import baostock as bs
    if bs.login().error_code != "0":
        print("login failed")
        return
    try:
        for code, sig, nxt in todo:
            try:
                cache[(code, sig, nxt)] = fetch_5m(bs_code(code), sig, nxt)
            except Exception as exc:  # noqa: BLE001
                print("fail", code, sig, exc, flush=True)
                cache[(code, sig, nxt)] = []
            pickle.dump(cache, open(CACHE, "wb"))
        print("fetched", len(todo))
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
