"""v3.1 A 级最终回测：121 个真实 14:30 样本 + 43 笔随机样本 + 合并检验。

输出 docs/overnight_v2/v3_1_backtest.json / v3_1_a_trades.csv
"""
import json
import pickle

import numpy as np
import pandas as pd

CACHE = "data/research/morning_exit_bars.pkl"
OUT = "docs/overnight_v2"
COST = 0.0025

from strategy_lab import load as load_panel


def load_a_sample():
    picks = pd.read_parquet("data/research/daily_picks.parquet")
    picks = picks[(picks["rank"] == 1) & (picks.trade_date >= "2025-01-01")].copy()
    picks = picks.rename(columns={"trade_date": "date", "ts_code": "code"})
    trades = pd.read_csv(f"{OUT}/daily_pick_trades.csv")
    trades["date"] = pd.to_datetime(trades.date)
    picks = picks.merge(trades[["date", "code", "net", "sell_time", "why"]], on=["date", "code"], how="inner")
    cache = pickle.loads(open(CACHE, "rb").read())
    panel = load_panel().set_index(["ts_code", "trade_date"])
    rows = []
    for r in picks.itertuples():
        bars = cache.get((r.code, str(r.date.date()), str(r.next_date)[:10]), [])
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
        ok = ((-7 <= pct1430 <= -2) and (pos1430 < 0.2) and (vwap_pos > -0.02)
              and (1.5 <= turn1430 <= 8) and (float(r.lu20) <= 1) and (float(r.ret5) > -0.02)
              and (float(r.amt_ratio) >= 0.8) and (float(r.b20) <= 0.70))
        if ok:
            rows.append({"date": r.date, "code": r.code, "net": float(r.net),
                         "pct1430": round(pct1430, 2), "pos1430": round(pos1430, 3),
                         "vwap_pos": round(vwap_pos, 4), "turn1430": round(turn1430, 2),
                         "sell_time": r.sell_time, "why": r.why})
    return pd.DataFrame(rows).sort_values("date")


def stats(d, label):
    daily = d.groupby("date").net.mean()
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    years = max((daily.index.max() - daily.index.min()).days / 365.25, 0.1)
    t = d.net.mean() / (d.net.std(ddof=1) / np.sqrt(len(d))) if len(d) > 2 else np.nan
    rng = np.random.default_rng(42)
    boots = [d.net.sample(len(d), replace=True, random_state=int(rng.integers(1e9))).mean()
             for _ in range(2000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "label": label, "n": int(len(d)), "days": int(len(daily)),
        "win": round(float((d.net > 0).mean()), 4), "avg": round(float(d.net.mean()), 4),
        "total": round(float(eq.iloc[-1] - 1), 4),
        "ann": round(float(eq.iloc[-1] ** (1 / years) - 1), 4),
        "dd": round(float(dd.min()), 4),
        "sharpe": round(float(daily.mean() / daily.std() * np.sqrt(252)), 2),
        "t": round(float(t), 2) if t == t else None,
        "boot_lo": round(float(lo), 4), "boot_hi": round(float(hi), 4),
        "boot_pos": round(float(np.mean(np.array(boots) > 0)), 3),
        "monthly": [{"ym": f"{y}-{m:02d}", "ret": round(float(v - 1), 4)}
                    for (y, m), v in (1 + daily).groupby([daily.index.year, daily.index.month]).prod().items()],
        "yearly": [{"year": int(y), "ret": round(float(v - 1), 4)}
                   for y, v in (1 + daily).groupby(daily.index.year).prod().items()],
        "by_exit": ({k: {"n": int(len(g)), "mean": round(float(g.net.mean()), 4),
                          "win": round(float((g.net > 0).mean()), 3)}
                      for k, g in d.groupby("why")} if "why" in d.columns else {}),
    }


def main():
    a = load_a_sample()
    v3 = pd.read_csv(f"{OUT}/v3_trades.csv")
    v3["date"] = pd.to_datetime(v3["signal_date"])
    v3 = v3.rename(columns={"net": "net"})[["date", "ts_code", "net"]].rename(columns={"ts_code": "code"})
    # 去重：同一 (date, code) 只保留一次
    a_key = a.assign(key=a.date.astype(str) + "|" + a.code)
    v3_key = v3.assign(key=v3.date.astype(str) + "|" + v3.code)
    overlap = len(set(a_key.key) & set(v3_key.key))
    comb = pd.concat([a_key[["date", "code", "net", "key"]], v3_key[["date", "code", "net", "key"]]])
    comb = comb.drop_duplicates("key")
    print("A sample", len(a), "43 sample", len(v3), "overlap", overlap, "combined", len(comb))

    res = {"a": stats(a, "v3.1 A 级 · 121 天"), "v3": stats(v3, "43 笔随机样本"),
           "combined": stats(comb, "合并（去重）"), "overlap": overlap}
    with open(f"{OUT}/v3_1_backtest.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    a.to_csv(f"{OUT}/v3_1_a_trades.csv", index=False, encoding="utf-8-sig")
    for k in ("a", "v3", "combined"):
        x = res[k]
        print(f"\n{k}: n={x['n']} win={x['win']:.1%} avg={x['avg']:+.2%} ann={x['ann']:+.1%} "
              f"dd={x['dd']:+.1%} t={x['t']} CI=[{x['boot_lo']:+.2%},{x['boot_hi']:+.2%}] "
              f"P(mean>0)={x['boot_pos']:.1%}")
        print("  yearly", x["yearly"], "monthly", len(x["monthly"]), "months")
        print("  exits", x["by_exit"])


if __name__ == "__main__":
    main()
