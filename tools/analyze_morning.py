"""Analyze cached 5m bars: 14:50 entry vs 15:00 close entry, and morning exit rules."""
import pickle
import numpy as np
import pandas as pd

CACHE = "data/research/morning_exit_bars.pkl"
cache = pickle.loads(open(CACHE, "rb").read())
print("cached signals", len(cache))


def entry_prices(bars, next_date):
    t = [b for b in bars if b["date"] < next_date]
    if not t:
        return None, None
    close = t[-1]["close"]
    b1450 = next((b for b in t if b["time"] == "1450"), None)
    return (b1450["close"] if b1450 else close), close


def evaluate(bars, next_date):
    e1450, eclose = entry_prices(bars, next_date)
    n = [b for b in bars if b["date"] == next_date]
    if not n or e1450 is None:
        return None
    by_t = {b["time"]: b for b in n}
    out = {"d_1450_close": eclose / e1450 - 1}
    for t in ("0935", "0940", "0950", "1000"):
        if t in by_t:
            out[f"r_{t}_1450"] = by_t[t]["close"] / e1450 - 1
            out[f"r_{t}_close"] = by_t[t]["close"] / eclose - 1
    # target +1% checked at 09:35 high, else 10:00 close
    if "0935" in by_t and "1000" in by_t:
        hit = by_t["0935"]["high"] >= e1450 * 1.01
        out["t1_0935_else_1000"] = (0.01 if hit else by_t["1000"]["close"] / e1450 - 1)
        out["t1_close_0935_else_1000"] = (0.01 if by_t["0935"]["high"] >= eclose * 1.01
                                          else by_t["1000"]["close"] / eclose - 1)
    # -2% stop from 09:30 to 10:00
    for s in (-0.015, -0.02, -0.03):
        ret = out.get("r_1000_1450")
        for b in n:
            if b["time"] > "1000":
                break
            if b["open"] <= e1450 * (1 + s):
                ret = b["open"] / e1450 - 1
                break
            if b["low"] <= e1450 * (1 + s):
                ret = s
                break
        out[f"stop{s}_1450"] = ret
    return out


rows = []
for (code, sig, nxt), bars in cache.items():
    r = evaluate(bars, nxt)
    if r:
        rows.append({"ts_code": code, "signal_date": sig, "next_date": nxt, **r})
df = pd.DataFrame(rows)
print("valid", len(df))

print("\n== 14:50 买入 vs 15:00 收盘买入 ==")
for c in ["d_1450_close", "r_0940_1450", "r_0940_close", "r_1000_1450", "r_1000_close"]:
    x = df[c].dropna()
    if len(x):
        print(f"{c:16s} mean={x.mean()*100:+.3f}% median={x.median()*100:+.3f}% "
              f"win={(x>0).mean()*100:.1f}% t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}")

print("\n== 早盘卖出规则（14:50 入场）==")
for c in ["t1_0935_else_1000", "stop-0.015_1450", "stop-0.02_1450", "stop-0.03_1450"]:
    x = df[c].dropna()
    if len(x):
        print(f"{c:20s} mean={x.mean()*100:+.3f}% median={x.median()*100:+.3f}% "
              f"win={(x>0).mean()*100:.1f}% t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}")
