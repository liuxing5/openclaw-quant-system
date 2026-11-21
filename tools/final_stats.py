"""Detailed stats for the refined rule (ALL filters) — trades, monthly, drawdown, cost."""
import json
import numpy as np
import pandas as pd

COST = 0.0025
p = pd.read_parquet("data/research/panic_enriched.parquet")
p["exit_gap"] = np.where(p.gap_open >= 0.005, p.gap_open,
                         np.where(p.gap_open <= -0.03, p.gap_open, p.stop2))
base = ((p.pos1430 < 0.2) & p.pct1430.between(-7, -2) & p.turn1430.between(1.5, 8) & (p.b20 <= 0.7))
filters = {
    "A_4f": (p.vwap_pos > -0.02) & (p.prev_lu20 <= 1) & (p.prev_ret5 > -0.02) & (p.amount_ratio_1430 >= 0.8),
    "B_3f": (p.vwap_pos > -0.02) & (p.prev_lu20 <= 1) & (p.prev_ret5 > -0.02),
}
all_days = pd.bdate_range("2025-01-01", "2026-04-21")

for name, f in filters.items():
    s = p[base & f].dropna(subset=["exit_gap"]).sort_values("signal_date").copy()
    picks = s.groupby("signal_date").head(5)
    daily = (picks.groupby("signal_date").exit_gap.mean() - COST).reindex(all_days).fillna(0)
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    print(f"\n===== {name}: n={len(s)} days={s.signal_date.nunique()} =====")
    x = s.exit_gap - COST
    print(f"per trade net mean={x.mean()*100:+.3f}% median={x.median()*100:+.3f}% "
          f"win={(x>0).mean()*100:.1f}% t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}")
    print(f"ann={(eq.iloc[-1])**(365.25/((daily.index.max()-daily.index.min()).days))-1:+.1%} "
          f"total={eq.iloc[-1]-1:+.1%} dd={dd.min():+.1%} "
          f"sharpe={daily.mean()/daily.std()*np.sqrt(252):.2f}")
    print("\nmonthly:")
    m = (1 + daily).groupby([daily.index.year, daily.index.month]).prod() - 1
    print(m.round(3).to_string())
    print("\ntrades:")
    print(s[["signal_date", "ts_code", "pct1430", "pos1430", "vwap_pos", "prev_lu20",
             "prev_ret5", "amount_ratio_1430", "exit_gap"]].assign(
        signal_date=s.signal_date.dt.strftime("%Y-%m-%d")).round(4).to_string(index=False))
    s.to_parquet(f"data/research/final_{name}.parquet", index=False)
