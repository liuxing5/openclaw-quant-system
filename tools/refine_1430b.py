"""Final refinement: exit variants, gap-based exit, full-period equity for the 14:30 rule."""
import numpy as np
import pandas as pd

d = pd.read_csv("data/research/1430_signals.csv")
d["signal_date"] = pd.to_datetime(d.signal_date)
COST = 0.0025

m = ((d.pos1430 < 0.2) & d.pct1430.between(-6.5, -2) & d.turn1430.between(1.5, 8) & (d.b20 <= 0.7))
s = d[m].dropna(subset=["r0940", "r0950", "r1000"]).copy()
print("signals", len(s), "days", s.signal_date.nunique())

# gap-based exit: 高开>+0.5% 开盘卖；否则 09:50 卖（-2% 止损）
gap = s.gap_open
s["exit_gap"] = np.where(gap >= 0.005, gap,
                         np.where(gap <= -0.03, gap, s["stop2"]))
print("\n== exit variants (per trade) ==")
for c in ["r0935", "r0940", "r0950", "r1000", "stop2", "exit_gap"]:
    x = s[c].dropna()
    print(f"  {c:9s} mean={x.mean()*100:+.3f}% median={x.median()*100:+.3f}% "
          f"win={(x>0).mean()*100:.1f}% t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}")

# daily equity over all trading days 2025-01-01..2026-04-21
all_days = pd.bdate_range("2025-01-01", "2026-04-21")
# 用信号日的交易日近似（bdate 含节假日，仅作年化分母）
for exit_col in ["r0940", "r0950", "stop2", "exit_gap"]:
    for mode in ("full", "slot20"):
        p = s.sort_values(["signal_date", "pos1430"]).groupby("signal_date").head(5)
        if mode == "full":
            daily = p.groupby("signal_date")[exit_col].mean() - COST
        else:
            daily = (p.groupby("signal_date")[exit_col].sum() * 0.2 / 1.0) - COST * p.groupby("signal_date").size() * 0.2
        daily = daily.reindex(all_days).fillna(0.0)
        eq = (1 + daily).cumprod()
        dd = (eq / eq.cummax() - 1).min()
        years = (daily.index.max() - daily.index.min()).days / 365.25
        tr = daily[daily.index < "2026-01-01"]
        te = daily[daily.index >= "2026-01-01"]
        print(f"\n{exit_col} {mode}: ann={eq.iloc[-1]**(1/years)-1:+.1%} total={eq.iloc[-1]-1:+.1%} "
              f"dd={dd:+.1%} sharpe={daily.mean()/daily.std()*np.sqrt(252):.2f} "
              f"2025 {(1+tr).prod()-1:+.1%} 2026 {(1+te).prod()-1:+.1%}")

# yearly per-trade
print("\n== per year (stop2) ==")
s["year"] = s.signal_date.dt.year
print(s.groupby("year")["stop2"].agg(["size", "mean", "median", lambda x: (x > 0).mean()]).round(4).to_string())
