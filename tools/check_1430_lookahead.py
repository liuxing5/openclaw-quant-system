"""Check look-ahead: re-apply trend filter with T-1 features (known at 14:30)."""
import numpy as np
import pandas as pd
from strategy_lab import load

d = pd.read_csv("data/research/1430_signals.csv")
d["signal_date"] = pd.to_datetime(d.signal_date)

panel = load()
panel = panel.sort_values(["ts_code", "trade_date"])
g = panel.groupby("ts_code", sort=False)
for c in ["close", "ma20", "ma60", "ret20"]:
    panel[f"prev_{c}"] = g[c].shift(1)
prev = panel[["ts_code", "trade_date", "prev_close", "prev_ma20", "prev_ma60", "prev_ret20"]]
d = d.merge(prev, left_on=["ts_code", "signal_date"], right_on=["ts_code", "trade_date"], how="left")

m = ((d.pos1430 < 0.2) & d.pct1430.between(-7, -2) & d.turn1430.between(1.5, 8) & (d.b20 <= 0.7))
s = d[m].dropna(subset=["exit_gap" if "exit_gap" in d else "stop2"]).copy()
s["exit_gap"] = np.where(s.gap_open >= 0.005, s.gap_open,
                         np.where(s.gap_open <= -0.03, s.gap_open, s.stop2))
s["trend_prev"] = (s.prev_ma20 > s.prev_ma60) & (s.prev_ret20 > 0.03) & (s.prev_close > s.prev_ma20)
print("signals", len(s), "trend_prev pass", int(s.trend_prev.sum()))
for flag, sub in [("all", s), ("trend_prev", s[s.trend_prev])]:
    x = sub.exit_gap.dropna()
    print(f"{flag}: n={len(x)} mean={x.mean()*100:+.3f}% win={(x>0).mean()*100:.1f}% "
          f"t={x.mean()/(x.std()/np.sqrt(len(x))):+.1f}")
