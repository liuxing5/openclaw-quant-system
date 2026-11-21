"""用真实 14:30 特征重新筛选每日 top1，看有多少是「14:30 当时真的合格」的。"""
import pickle

import numpy as np
import pandas as pd

CACHE = "data/research/morning_exit_bars.pkl"
from strategy_lab import load as load_panel


def main():
    picks = pd.read_parquet("data/research/daily_picks.parquet")
    picks = picks[(picks["rank"] == 1) & (picks.trade_date >= "2025-01-01")].copy()
    picks = picks.rename(columns={"trade_date": "date", "ts_code": "code"})
    trades = pd.read_csv("docs/overnight_v2/daily_pick_trades.csv")
    trades["date"] = pd.to_datetime(trades.date)
    picks = picks.merge(trades[["date", "code", "net", "sell_time", "why"]], on=["date", "code"], how="inner")
    cache = pickle.loads(open(CACHE, "rb").read())
    panel = load_panel().set_index(["ts_code", "trade_date"])

    rows = []
    for r in picks.itertuples():
        key_bar = (r.code, str(r.date.date()), str(r.next_date)[:10])
        bars = cache.get(key_bar, [])
        if not bars or "volume" not in bars[0]:
            continue
        pr = panel.loc[(r.code, r.date)]
        t = [b for b in bars if b["date"] == str(r.date.date())]
        upto = [b for b in t if b["time"] <= "1430"]
        if not t or not upto:
            continue
        price = upto[-1]["close"]
        hi = max(b["high"] for b in upto)
        lo = min(b["low"] for b in upto)
        vol_s = sum(b["volume"] for b in upto)
        vol_d = sum(b["volume"] for b in t)
        vwap = sum(b["close"] * b["volume"] for b in upto) / vol_s if vol_s else np.nan
        factor = float(pr["close"]) / float(t[-1]["close"]) if t[-1]["close"] else np.nan
        pre_close = float(pr["close"]) / (1 + float(pr["pct_chg"]) / 100)
        pct1430 = (price * factor / pre_close - 1) * 100
        pos1430 = (price - lo) / (hi - lo) if hi > lo else np.nan
        vwap_pos = price / vwap - 1 if vwap and vwap == vwap else np.nan
        turn1430 = float(pr["turnover_rate"]) * vol_s / vol_d if vol_d else np.nan
        pass_ = (
            (-7 <= pct1430 <= -2) and (pos1430 < 0.2) and (vwap_pos > -0.02)
            and (1.5 <= turn1430 <= 8) and (float(r.lu20) <= 1) and (float(r.ret5) > -0.02)
            and (float(r.amt_ratio) >= 0.8) and (float(r.b20) <= 0.70)
        )
        rows.append({"date": r.date, "code": r.code, "net": r.net, "pct1430": pct1430,
                     "pos1430": pos1430, "vwap_pos": vwap_pos, "turn1430": turn1430,
                     "pass": pass_})
    x = pd.DataFrame(rows)
    print("evaluated", len(x), "pass", int(x["pass"].sum()), "pass_rate", round(x["pass"].mean(), 3))
    for label, sub in [("all", x), ("pass", x[x["pass"]])]:
        if len(sub) < 5:
            print(label, "too few")
            continue
        daily = sub.groupby("date").net.mean()
        eq = (1 + daily).cumprod()
        dd = (eq / eq.cummax() - 1).min()
        years = (daily.index.max() - daily.index.min()).days / 365.25
        print(f"{label}: n={len(sub)} days={len(daily)} win={(sub.net>0).mean():.3f} "
              f"avg={sub.net.mean():.4f} ann={eq.iloc[-1]**(1/years)-1:+.3f} dd={dd:+.3f}")
    bad = x[~x["pass"]]
    print("\n未通过原因（各条件不满足占比）：")
    for col, cond in [("pct", ~bad.pct1430.between(-7, -2)), ("pos", ~(bad.pos1430 < 0.2)),
                      ("vwap", ~(bad.vwap_pos > -0.02)), ("turn", ~bad.turn1430.between(1.5, 8))]:
        print(f"  {col}: {cond.mean():.2%}")


if __name__ == "__main__":
    main()
