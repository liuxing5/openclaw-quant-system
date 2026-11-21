"""Assemble research datasets:
A) Yang classic 608 signals + 10m bars -> morning returns
B) panic 600 signals + VWAP/volume features + T-1 daily features
"""
import os
import pickle

import numpy as np
import pandas as pd
import psycopg2

DSN = ("postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl"
       "@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres")
OUT = "data/research"


def load_yang():
    conn = psycopg2.connect(DSN, connect_timeout=30)
    try:
        cur = conn.cursor()
        cur.execute("""
            select signal_date, ts_code, daily_pct, turnover_rate, cap_est, entry_price,
                   vwap, new_high, pullback, above_vwap, stronger, confirmed,
                   next_trade_date, ret_1030, max_return
            from classic_backtest_trades
        """)
        cols = [d[0] for d in cur.description]
        trades = pd.DataFrame(cur.fetchall(), columns=cols)
        cur.execute("""
            select signal_date, ts_code, bar_time, open, high, low, close
            from classic_backtest_10m
        """)
        cols = [d[0] for d in cur.description]
        bars = pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()
    trades["signal_date"] = pd.to_datetime(trades["signal_date"])
    bars["bar_time"] = bars["bar_time"].astype(str)
    bars["signal_date"] = pd.to_datetime(bars["signal_date"])
    for c in ["entry_price", "vwap", "daily_pct", "turnover_rate", "cap_est", "ret_1030"]:
        trades[c] = pd.to_numeric(trades[c], errors="coerce")
    for c in ["open", "high", "low", "close"]:
        bars[c] = pd.to_numeric(bars[c], errors="coerce")

    piv = bars.pivot_table(index=["signal_date", "ts_code"], columns="bar_time",
                           values=["open", "close", "high", "low"], aggfunc="last")
    piv.columns = [f"{a}_{b}" for a, b in piv.columns]
    piv = piv.reset_index()
    d = trades.merge(piv, on=["signal_date", "ts_code"], how="left")
    e = d["entry_price"]
    for hm in ["09:40:00", "09:50:00", "10:00:00", "10:30:00"]:
        col = f"close_{hm}"
        if col in d:
            d[f"ret_{hm[:2]}{hm[3:5]}"] = d[col] / e - 1
    d["ret_open"] = d.get("open_09:40:00", np.nan) / e - 1
    d["hi_before_1000"] = d[[c for c in d.columns if c.startswith("high_") and c <= "high_10:00:00"]].max(axis=1) / e - 1
    d["vwap_pos"] = e / d["vwap"] - 1
    d.to_parquet(os.path.join(OUT, "yang_signals.parquet"), index=False)
    print("yang", d.shape, "confirmed", int(d.confirmed.sum()))
    return d


def enrich_panic():
    d = pd.read_csv(os.path.join(OUT, "1430_signals.csv"))
    d["signal_date"] = pd.to_datetime(d.signal_date)

    # VWAP / volume from cached 5m bars (only bars with volume)
    cache = pickle.loads(open(os.path.join(OUT, "morning_exit_bars.pkl"), "rb").read())
    feats = []
    for r in d.itertuples():
        sig = str(r.signal_date.date())
        bars = cache.get((r.ts_code, sig, r.next_date), [])
        if not bars or "volume" not in bars[0]:
            feats.append({})
            continue
        t = [x for x in bars if x["date"] == sig]
        upto = [x for x in t if x["time"] <= "1430"]
        vol = sum(x["volume"] for x in upto)
        vwap = sum(x["close"] * x["volume"] for x in upto) / vol if vol else np.nan
        feats.append({
            "vwap1430": vwap,
            "vwap_pos": upto[-1]["close"] / vwap - 1 if vwap and vwap == vwap else np.nan,
            "vol_frac": vol / sum(x["volume"] for x in t) if t else np.nan,
        })
    d = pd.concat([d.reset_index(drop=True), pd.DataFrame(feats)], axis=1)

    # T-1 daily features from panel
    from strategy_lab import load
    p = load().sort_values(["ts_code", "trade_date"])
    g = p.groupby("ts_code", sort=False)
    feat_cols = ["close", "ma5", "ma10", "ma20", "ma60", "ret5", "ret20", "amount",
                 "amount_ma20", "turnover_rate", "circ_mcap_yi", "b20"]
    for c in feat_cols:
        p[f"prev_{c}"] = g[c].shift(1)
    # 20 日涨停基因（含 T-1 往前 20 日，不含今日）
    p["prev_lu20"] = g["is_limit_up"].transform(lambda s: s.shift(1).rolling(20).sum())
    keep = ["ts_code", "trade_date"] + [f"prev_{c}" for c in feat_cols] + ["prev_lu20"]
    d = d.merge(p[keep], left_on=["ts_code", "signal_date"], right_on=["ts_code", "trade_date"], how="left")
    d["board"] = np.where(d.ts_code.str.startswith(("300", "301", "688", "689")), "创业/科创", "主板")
    d["amount_ratio_1430"] = d["prev_amount"] * d["vol_frac"] / d["prev_amount_ma20"]
    d.to_parquet(os.path.join(OUT, "panic_enriched.parquet"), index=False)
    print("panic", d.shape, "vwap non-null", int(d.vwap_pos.notna().sum()))
    return d


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    yang = load_yang()
    panic = enrich_panic()
