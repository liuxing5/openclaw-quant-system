"""Refine the 14:30 rule on the 600-signal 5m dataset and build a portfolio curve."""
import numpy as np
import pandas as pd

d = pd.read_csv("data/research/1430_signals.csv")
d["signal_date"] = pd.to_datetime(d.signal_date)
COST = 0.0025

variants = {
    "base(pos<.4,pct-6~-1.5)": (d.pos1430 < 0.4) & d.pct1430.between(-6, -1.5) & d.turn1430.between(1.5, 8),
    "pos<.2": (d.pos1430 < 0.2) & d.pct1430.between(-6.5, -2) & d.turn1430.between(1.5, 8),
    "pos<.2 & b20<.7": (d.pos1430 < 0.2) & d.pct1430.between(-6.5, -2) & d.turn1430.between(1.5, 8) & (d.b20 <= 0.7),
    "pos<.2 & pct<-3": (d.pos1430 < 0.2) & d.pct1430.between(-6.5, -3) & d.turn1430.between(1.5, 8) & (d.b20 <= 0.7),
    "pos<.3": (d.pos1430 < 0.3) & d.pct1430.between(-6.5, -2) & d.turn1430.between(1.5, 8) & (d.b20 <= 0.7),
}

for name, m in variants.items():
    s = d[m].dropna(subset=["r0940"])
    if len(s) < 10:
        print(name, "n", len(s), "too few")
        continue
    print(f"\n== {name} n={len(s)} days={s.signal_date.nunique()} ==")
    for c in ["r0935", "r0940", "r0950", "r1000", "stop2"]:
        x = s[c].dropna()
        if len(x) > 5:
            print(f"  {c:6s} mean={x.mean()*100:+.3f}% median={x.median()*100:+.3f}% "
                  f"win={(x>0).mean()*100:.1f}% t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}")
    # portfolio: top 5 per day by pos1430, equal weight, cost
    p = s.sort_values(["signal_date", "pos1430"]).groupby("signal_date").head(5)
    for exit_col in ["r0940", "r0950", "r1000"]:
        daily = p.groupby("signal_date")[exit_col].mean() - COST
        eq = (1 + daily).cumprod()
        dd = (eq / eq.cummax() - 1).min()
        years = (daily.index.max() - daily.index.min()).days / 365.25
        tr = daily[daily.index < "2026-01-01"]
        te = daily[daily.index >= "2026-01-01"]
        print(f"  portfolio {exit_col}: days={len(daily)} avg={daily.mean()*100:+.3f}% "
              f"total={eq.iloc[-1]-1:+.1%} ann={eq.iloc[-1]**(1/years)-1:+.1%} dd={dd:+.1%} "
              f"sharpe={daily.mean()/daily.std()*np.sqrt(252):.2f} | "
              f"2025 {len(tr)}d {(1+tr).prod()-1:+.1%} | 2026 {len(te)}d {(1+te).prod()-1:+.1%}")
