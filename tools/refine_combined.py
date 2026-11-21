"""Refined 14:30 rule: add VWAP proximity, limit-up gene, 5-day trend, volume filters."""
import numpy as np
import pandas as pd

COST = 0.0025
p = pd.read_parquet("data/research/panic_enriched.parquet")
p["exit_gap"] = np.where(p.gap_open >= 0.005, p.gap_open,
                         np.where(p.gap_open <= -0.03, p.gap_open, p.stop2))
base = ((p.pos1430 < 0.2) & p.pct1430.between(-7, -2) & p.turn1430.between(1.5, 8) & (p.b20 <= 0.7))
all_days = pd.bdate_range("2025-01-01", "2026-04-21")


def show(name, m, exit_col="exit_gap"):
    s = p[base & m].dropna(subset=[exit_col]).sort_values(["signal_date", "pos1430"])
    if len(s) < 15:
        print(f"{name:38s} n={len(s):3d} too few")
        return
    x = s[exit_col]
    picks = s.groupby("signal_date").head(5)
    daily = (picks.groupby("signal_date")[exit_col].mean() - COST).reindex(all_days).fillna(0)
    eq = (1 + daily).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    years = (daily.index.max() - daily.index.min()).days / 365.25
    tr = daily[daily.index < "2026-01-01"]
    te = daily[daily.index >= "2026-01-01"]
    n25 = int((s.signal_date < "2026-01-01").sum())
    print(f"{name:38s} n={len(s):3d} 25/26={n25}/{len(s)-n25} avg={x.mean()*100:+.3f}% "
          f"win={(x>0).mean()*100:.1f}% t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f} "
          f"| ann={eq.iloc[-1]**(1/years)-1:+.1%} dd={dd:+.1%} | 25 {(1+tr).prod()-1:+.1%} 26 {(1+te).prod()-1:+.1%}")


show("base", pd.Series(True, index=p.index))
show("+vwap>-2%", p.vwap_pos > -0.02)
show("+lu20<=1", p.prev_lu20 <= 1)
show("+ret5>-2%", p.prev_ret5 > -0.02)
show("+amt_ratio>=0.8", p.amount_ratio_1430 >= 0.8)
show("+vwap>-2% +lu20<=1", (p.vwap_pos > -0.02) & (p.prev_lu20 <= 1))
show("+vwap>-2% +ret5>-2%", (p.vwap_pos > -0.02) & (p.prev_ret5 > -0.02))
show("ALL: vwap>-2, lu<=1, ret5>-2, amt>=.8",
     (p.vwap_pos > -0.02) & (p.prev_lu20 <= 1) & (p.prev_ret5 > -0.02) & (p.amount_ratio_1430 >= 0.8))
show("ALL - amt", (p.vwap_pos > -0.02) & (p.prev_lu20 <= 1) & (p.prev_ret5 > -0.02))
show("ALL (vwap>-1%)", (p.vwap_pos > -0.01) & (p.prev_lu20 <= 1) & (p.prev_ret5 > -0.02) & (p.amount_ratio_1430 >= 0.8))
