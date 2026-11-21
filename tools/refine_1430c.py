"""Variant table for the 14:30 rule using gap-based exit."""
import numpy as np
import pandas as pd

d = pd.read_csv("data/research/1430_signals.csv")
d["signal_date"] = pd.to_datetime(d.signal_date)
COST = 0.0025
all_days = pd.bdate_range("2025-01-01", "2026-04-21")

d["exit_gap"] = np.where(d.gap_open >= 0.005, d.gap_open,
                         np.where(d.gap_open <= -0.03, d.gap_open, d.stop2))

variants = {
    "pos<.2 pct-6.5~-2 b20<=.7": (d.pos1430 < 0.2) & d.pct1430.between(-6.5, -2) & (d.b20 <= 0.7),
    "pos<.3 pct-6.5~-2 b20<=.7": (d.pos1430 < 0.3) & d.pct1430.between(-6.5, -2) & (d.b20 <= 0.7),
    "pos<.2 pct-8~-2 b20<=.7": (d.pos1430 < 0.2) & d.pct1430.between(-8, -2) & (d.b20 <= 0.7),
    "pos<.2 pct-6.5~-2 b20<=.8": (d.pos1430 < 0.2) & d.pct1430.between(-6.5, -2) & (d.b20 <= 0.8),
    "pos<.2 pct-6.5~-1.5 b20<=.7": (d.pos1430 < 0.2) & d.pct1430.between(-6.5, -1.5) & (d.b20 <= 0.7),
}
for name, m in variants.items():
    s = d[m & d.turn1430.between(1.5, 8)].dropna(subset=["exit_gap"])
    if len(s) < 20:
        continue
    p = s.sort_values(["signal_date", "pos1430"]).groupby("signal_date").head(5)
    daily = (p.groupby("signal_date").exit_gap.mean() - COST).reindex(all_days).fillna(0)
    eq = (1 + daily).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    years = (daily.index.max() - daily.index.min()).days / 365.25
    tr = daily[daily.index < "2026-01-01"]
    te = daily[daily.index >= "2026-01-01"]
    x = s.exit_gap
    print(f"{name:32s} n={len(s):3d} days={s.signal_date.nunique():3d} "
          f"avg={x.mean()*100:+.3f}% win={(x>0).mean()*100:.0f}% | "
          f"ann={eq.iloc[-1]**(1/years)-1:+.1%} dd={dd:+.1%} sh={daily.mean()/daily.std()*np.sqrt(252):.2f} "
          f"2025 {(1+tr).prod()-1:+.1%} 2026 {(1+te).prod()-1:+.1%}")
