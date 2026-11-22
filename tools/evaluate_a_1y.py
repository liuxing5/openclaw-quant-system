"""评估 1 年 A 级 150 只样本：真实 14:30 过滤 + plan A 等权。"""
import json
import pickle

import numpy as np
import pandas as pd

CACHE = "data/research/morning_exit_bars.pkl"
OUT = "docs/overnight_v2"
COST = 0.0025


def simulate(bars, sig, nxt, entry):
    n = [b for b in bars if b["date"] == nxt]
    if not n:
        return None
    gap = n[0]["open"] / entry - 1
    if gap >= 0.005:
        return gap, "高开落袋", "09:30"
    if gap <= -0.03:
        return gap, "深水砍", "09:30"
    stop = entry * 0.98
    for b in n:
        if b["time"] > "1000":
            break
        if b["open"] <= stop:
            return b["open"] / entry - 1, "止损(跳空)", b["time"][:2] + ":" + b["time"][2:]
        if b["low"] <= stop:
            return -0.02, "−2%止损", b["time"][:2] + ":" + b["time"][2:]
    last = next((b for b in n if b["time"] == "1000"), n[-1])
    return last["close"] / entry - 1, "持到10:00", "10:00"


def main():
    s = pd.read_parquet("data/research/a_1y_sample.parquet")
    cache = pickle.loads(open(CACHE, "rb").read())
    rows, miss = [], 0
    for r in s.itertuples():
        bars = cache.get((r.ts_code, r.sig_date, r.next_date), [])
        if not bars or "volume" not in bars[0]:
            miss += 1
            continue
        t = [b for b in bars if b["date"] == r.sig_date]
        upto = [b for b in t if b["time"] <= "1430"]
        if not t or not upto:
            miss += 1
            continue
        price = upto[-1]["close"]
        hi = max(b["high"] for b in upto)
        lo = min(b["low"] for b in upto)
        vol_s = sum(b["volume"] for b in upto)
        vol_d = sum(b["volume"] for b in t)
        vwap = sum(b["close"] * b["volume"] for b in upto) / vol_s if vol_s else np.nan
        factor = float(r.close) / float(t[-1]["close"]) if t[-1]["close"] else np.nan
        pre_close = float(r.close) / (1 + float(r.pct_chg) / 100)
        pct1430 = (price * factor / pre_close - 1) * 100
        pos1430 = (price - lo) / (hi - lo) if hi > lo else np.nan
        vwap_pos = price / vwap - 1 if vwap and vwap == vwap else np.nan
        turn1430 = float(r.turnover_rate) * vol_s / vol_d if vol_d else np.nan
        ok = ((-7 <= pct1430 <= -2) and (pos1430 < 0.2) and (vwap_pos > -0.02)
              and (1.5 <= turn1430 <= 8) and (float(r.lu20) <= 1) and (float(r.prev_ret5) > -0.02)
              and (float(r.amount_ratio) >= 0.8))
        if not ok:
            continue
        ebar = next((b for b in t if b["time"] == "1450"), None)
        entry = ebar["close"] if ebar else price
        sim = simulate(bars, r.sig_date, r.next_date, entry)
        if sim is None:
            continue
        gross, why, st = sim
        rows.append({"date": pd.Timestamp(r.sig_date), "code": r.ts_code,
                     "entry": round(entry, 2), "sell_time": st,
                     "sell": round(entry * (1 + gross), 2), "why": why,
                     "gross": round(gross, 4), "net": round(gross - COST, 4),
                     "pct1430": round(pct1430, 2), "pos1430": round(pos1430, 3),
                     "vwap_pos": round(vwap_pos, 4), "turn1430": round(turn1430, 2)})
    d = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    print("sample", len(s), "bars", len(s) - miss, "pass_true_A", len(d),
          "pass_rate", round(len(d) / max(len(s) - miss, 1), 3))
    if len(d) < 5:
        print("too few")
        return
    daily = d.groupby("date").net.mean()
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    years = max((daily.index.max() - daily.index.min()).days / 365.25, 0.1)
    rng = np.random.default_rng(42)
    boots = [daily.sample(len(daily), replace=True, random_state=int(rng.integers(1e9))).mean()
             for _ in range(3000)]
    res = {
        "n": int(len(d)), "days": int(len(daily)),
        "names_per_day": round(float(d.groupby("date").size().mean()), 2),
        "win": round(float((d.net > 0).mean()), 4),
        "avg": round(float(d.net.mean()), 4),
        "total": round(float(eq.iloc[-1] - 1), 4),
        "ann": round(float(eq.iloc[-1] ** (1 / years) - 1), 4),
        "dd": round(float(dd.min()), 4),
        "sharpe": round(float(daily.mean() / daily.std() * np.sqrt(252)), 2),
        "t": round(float(daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily)))), 2),
        "boot_lo": round(float(np.percentile(boots, 2.5)), 4),
        "boot_hi": round(float(np.percentile(boots, 97.5)), 4),
        "by_exit": {k: {"n": int(len(g)), "mean": round(float(g.net.mean()), 4)}
                    for k, g in d.groupby("why")},
        "monthly": [{"ym": f"{y}-{m:02d}", "ret": round(float(v - 1), 4)}
                    for (y, m), v in (1 + daily).groupby([daily.index.year, daily.index.month]).prod().items()],
    }
    print(json.dumps(res, ensure_ascii=False, indent=1))
    with open(f"{OUT}/a_1y_backtest.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    d.to_csv(f"{OUT}/a_1y_trades.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
