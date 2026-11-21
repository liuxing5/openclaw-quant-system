"""Per-rule 14:50-entry morning-exit stats using cached 5m bars."""
import pickle
import numpy as np
import pandas as pd

cache = pickle.loads(open("data/research/morning_exit_bars.pkl", "rb").read())
sig = pd.read_csv("data/research/morning_exit.csv")
rule_map = {(r.ts_code, r.signal_date): r.rule for r in sig.itertuples()}


def ev(bars, nxt):
    t = [b for b in bars if b["date"] < nxt]
    n = [b for b in bars if b["date"] == nxt]
    if not t or not n:
        return None
    b1450 = next((b for b in t if b["time"] == "1450"), None)
    e = b1450["close"] if b1450 else t[-1]["close"]
    by = {b["time"]: b for b in n}
    out = {}
    for k in ("0935", "0940", "0950", "1000"):
        if k in by:
            out[f"r{k}"] = by[k]["close"] / e - 1
    # -2% stop
    ret = out.get("r1000")
    for b in n:
        if b["time"] > "1000":
            break
        if b["open"] <= e * 0.98:
            ret = b["open"] / e - 1
            break
        if b["low"] <= e * 0.98:
            ret = -0.02
            break
    out["stop2"] = ret
    return out


rows = []
for (code, s, nxt), bars in cache.items():
    r = ev(bars, nxt)
    if r:
        rows.append({"rule": rule_map.get((code, s), "?"), **r})
df = pd.DataFrame(rows)
for rule, g in df.groupby("rule"):
    print(f"\n== {rule} n={len(g)} ==")
    for c in ["r0935", "r0940", "r0950", "r1000", "stop2"]:
        x = g[c].dropna()
        if len(x) > 5:
            print(f"  {c:6s} mean={x.mean()*100:+.3f}% median={x.median()*100:+.3f}% "
                  f"win={(x>0).mean()*100:.1f}% t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}")
