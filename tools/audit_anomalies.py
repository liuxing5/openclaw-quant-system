"""数据完整性审计：买入/卖出价是否落在当日 5m 高低区间；T+1 是否真的是下一交易日；
5m 复权因子在 T/T+1 是否一致（除权会改变因子，收益仍为全收益）。"""
import json
import pickle

import numpy as np
import pandas as pd
import psycopg2

CACHE = "data/research/morning_exit_bars.pkl"
CORE = "docs/overnight_v2/v3_trades.csv"
DSN = ("postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl"
       "@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres")


def main():
    core = pd.read_csv(CORE).rename(columns={"signal_date": "date", "ts_code": "code"})
    core["date"] = pd.to_datetime(core["date"])
    cache = pickle.loads(open(CACHE, "rb").read())
    from strategy_lab import load as load_panel
    daily = load_panel().set_index(["ts_code", "trade_date"])
    panel = pd.read_parquet("data/research/panic_enriched.parquet")
    panel["signal_date"] = pd.to_datetime(panel.signal_date)
    panel = panel.set_index(["ts_code", "signal_date"])

    # 名称
    conn = psycopg2.connect(DSN, connect_timeout=30)
    try:
        cur = conn.cursor()
        cur.execute("select ts_code, stock_name from stock_basic_info")
        names = dict(cur.fetchall())
    finally:
        conn.close()

    flags = []
    rows = []
    for r in core.itertuples():
        sig = str(r.date.date())
        rec = panel.loc[(r.code, r.date)] if (r.code, r.date) in panel.index else None
        nxt = rec["next_date"] if rec is not None else None
        bars = cache.get((r.code, sig, nxt), [])
        t = [b for b in bars if b["date"] == sig]
        n = [b for b in bars if b["date"] == nxt]
        if not t or not n:
            flags.append((sig, r.code, "缺少 5m 数据"))
            continue
        ebar = next((b for b in t if b["time"] == "1450"), t[-1])
        entry = ebar["close"]
        t_low = min(b["low"] for b in t)
        t_high = max(b["high"] for b in t)
        if not (t_low <= entry <= t_high):
            flags.append((sig, r.code, f"买入价不在T日区间 {entry} [{t_low},{t_high}]"))

        by = {b["time"]: b for b in n}
        open_px = n[0]["open"]
        gap = open_px / entry - 1
        if gap >= 0.005 or gap <= -0.03:
            sell = open_px
            sell_time = "09:30"
        else:
            stop = entry * 0.98
            sell = None
            for b in n:
                if b["time"] > "1000":
                    break
                if b["open"] <= stop:
                    sell, sell_time = b["open"], b["time"]
                    break
                if b["low"] <= stop:
                    sell, sell_time = stop, b["time"]
                    break
            if sell is None:
                sell, sell_time = by["1000"]["close"], "10:00"
        n_low = min(b["low"] for b in n)
        n_high = max(b["high"] for b in n)
        if not (n_low <= sell <= n_high):
            flags.append((sig, r.code, f"卖出价不在T+1区间 {sell} [{n_low},{n_high}]"))

        # 复权因子一致性：5m T 15:00 close / 面板 raw close；5m T+1 close / 面板 f_close1
        prow = daily.loc[(r.code, r.date)]
        factor_t = float(t[-1]["close"]) / float(prow["close"])
        factor_n = float(n[-1]["close"]) / float(prow["f_close1"])
        if abs(factor_n / factor_t - 1) > 0.005:
            flags.append((sig, r.code, f"T/T+1 复权因子变化 {factor_t:.4f}->{factor_n:.4f}（除权）"))

        rows.append({"date": sig, "code": r.code, "name": names.get(r.code, ""),
                     "entry": round(entry, 3), "t_low": round(t_low, 3), "t_high": round(t_high, 3),
                     "sell": round(sell, 3), "sell_time": sell_time,
                     "n_low": round(n_low, 3), "n_high": round(n_high, 3),
                     "factor_t": round(factor_t, 4), "factor_n": round(factor_n, 4)})

    print("trades", len(rows), "flags", len(flags))
    for f in flags:
        print("FLAG", f)
    print("\nnames sample:", [(x['code'], x['name']) for x in rows[:5]])
    with open("docs/overnight_v2/v3_anomalies.json", "w", encoding="utf-8") as f:
        json.dump({"flags": flags, "rows": rows}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
