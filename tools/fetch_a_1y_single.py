"""单进程拉剩余 A 级样本 5m（socket 超时，逐只落盘）。"""
import os
import pickle
import socket

import pandas as pd

socket.setdefaulttimeout(8)
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
    s = pd.read_parquet(SAMPLE)
    cache = pickle.loads(open(CACHE, "rb").read()) if os.path.exists(CACHE) else {}
    todo = []
    for r in s.itertuples():
        key = (r.ts_code, r.sig_date, r.next_date)
        if key not in cache or not cache[key] or "volume" not in cache[key][0]:
            todo.append(key)
    print("todo", len(todo), flush=True)
    if not todo:
        return
    import baostock as bs
    lg = bs.login()
    print("login", lg.error_code, flush=True)
    if lg.error_code != "0":
        return
    try:
        for i, (code, sig, nxt) in enumerate(todo):
            try:
                cache[(code, sig, nxt)] = fetch_5m(bs_code(code), sig, nxt)
            except Exception as exc:  # noqa: BLE001
                print("fail", code, sig, type(exc).__name__, flush=True)
                cache[(code, sig, nxt)] = []
            if i % 5 == 0:
                pickle.dump(cache, open(CACHE, "wb"))
                print("progress", i, flush=True)
        pickle.dump(cache, open(CACHE, "wb"))
        print("done", flush=True)
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
