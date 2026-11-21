"""Univariate analysis: Yang classic vs panic, with the shared morning-exit rule."""
import numpy as np
import pandas as pd

COST = 0.0025

# ---------------- Yang ----------------
y = pd.read_parquet("data/research/yang_signals.parquet")
y["gap"] = y["open_09:40:00"] / y["entry_price"] - 1
# 09:30 开盘价 ≈ 第一根 10m bar 的 open；若缺失用 09:40 close 近似
y["gap"] = y["gap"].fillna(y["ret_0940"])
ret = y["ret_1000"].copy()
for i, r in y.iterrows():
    if pd.isna(r["low_09:40:00"]):
        continue
    stop = r["entry_price"] * 0.98
    lows = [r.get("low_09:40:00"), r.get("low_09:50:00"), r.get("low_10:00:00")]
    if min([x for x in lows if pd.notna(x)] or [np.inf]) <= stop:
        ret.iloc[i] = -0.02
y["exit_gap"] = np.where(y["gap"] >= 0.005, y["gap"],
                         np.where(y["gap"] <= -0.03, y["gap"], ret))
y["ret_0940"] = pd.to_numeric(y["ret_0940"], errors="coerce")
y["ret_1000"] = pd.to_numeric(y["ret_1000"], errors="coerce")

print("== Yang 608 signals (entry 14:30, exit morning) ==")
for flag in ["all", "confirmed"]:
    s = y if flag == "all" else y[y.confirmed]
    print(f"\n{flag} n={len(s)}")
    for c in ["ret_0940", "ret_1000", "ret_1030", "exit_gap"]:
        x = s[c].dropna()
        if len(x):
            print(f"  {c:9s} mean={x.mean()*100:+.3f}% win={(x>0).mean()*100:.1f}% "
                  f"t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}")

print("\n== Yang confirmed by feature ==")
yc = y[y.confirmed].copy()
for col in ["above_vwap", "stronger", "new_high", "pullback"]:
    for v in [True, False]:
        s = yc[yc[col] == v]
        if len(s) < 20:
            continue
        x = s.exit_gap.dropna()
        print(f"  {col}={v} n={len(x):3d} mean={x.mean()*100:+.3f}% win={(x>0).mean()*100:.1f}%")
for col, bins in [("daily_pct", [3, 3.5, 4, 4.5, 5]), ("turnover_rate", [5, 6.5, 8, 10]),
                  ("cap_est", [5e9, 8e9, 1.2e10, 2e10])]:
    s = yc.copy()
    s["b"] = pd.cut(s[col], bins)
    g = s.groupby("b", observed=True)["exit_gap"].agg(["size", "mean", lambda x: (x > 0).mean()])
    print(f"\n  {col}:\n{g.round(4).to_string()}")

# ---------------- Panic ----------------
p = pd.read_parquet("data/research/panic_enriched.parquet")
p["exit_gap"] = np.where(p.gap_open >= 0.005, p.gap_open,
                         np.where(p.gap_open <= -0.03, p.gap_open, p.stop2))
base = ((p.pos1430 < 0.2) & p.pct1430.between(-7, -2) & p.turn1430.between(1.5, 8) & (p.b20 <= 0.7))
s = p[base].dropna(subset=["exit_gap"]).copy()
print(f"\n\n== Panic base n={len(s)} days={s.signal_date.nunique()} ==")
x = s.exit_gap
print(f"  mean={x.mean()*100:+.3f}% win={(x>0).mean()*100:.1f}% t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}")

for col, bins in [("vwap_pos", [-0.05, -0.03, -0.02, -0.01, 0.0]),
                  ("prev_lu20", [-0.1, 0, 1, 2, 5]),
                  ("prev_ret5", [-0.15, -0.05, 0, 0.05, 0.15]),
                  ("amount_ratio_1430", [0, 0.8, 1.2, 2, 5]),
                  ("prev_ma5", [0])]:
    if col == "prev_ma5":
        s["ma_align"] = ((s.entry > s.prev_ma5) & (s.prev_ma5 > s.prev_ma10) & (s.prev_ma10 > s.prev_ma20)).astype(int)
        g = s.groupby("ma_align")["exit_gap"].agg(["size", "mean", lambda x: (x > 0).mean()])
        print(f"\n  ma_align:\n{g.round(4).to_string()}")
        continue
    s["b"] = pd.cut(s[col], bins)
    g = s.groupby("b", observed=True)["exit_gap"].agg(["size", "mean", lambda x: (x > 0).mean()])
    print(f"\n  {col}:\n{g.round(4).to_string()}")

print("\n  board:")
print(s.groupby("board")["exit_gap"].agg(["size", "mean", lambda x: (x > 0).mean()]).round(4).to_string())
print("\n  vwap_pos>0 vs <0:")
print(s.assign(v=s.vwap_pos > 0).groupby("v")["exit_gap"].agg(["size", "mean", lambda x: (x > 0).mean()]).round(4).to_string())
