"""Sweep the 'close position' condition and ranking for v2."""
import numpy as np
import pandas as pd
from strategy_lab import load, universe

df = load()
u = universe(df)
mkt = df.groupby("trade_date").agg(mkt=("pct_chg", "mean"), mb20=("b20", "first"))
mkt["m_idx"] = (1 + mkt["mkt"] / 100).cumprod()
mkt["m_ma10"] = mkt["m_idx"].rolling(10).mean()
mcols = ["m_idx", "m_ma10", "mb20"]
df = df.drop(columns=[c for c in mcols if c in df.columns]).merge(mkt[mcols], left_on="trade_date", right_index=True, how="left")
df["rank_gap"] = df["gap"]
df["rank_close_pos"] = df["close_pos"]

base = (
    (df.m_idx > df.m_ma10) & (df.mb20 >= 0.30)
    & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > 0.03)
    & (df.pct_chg.between(-6, -1.5))
    & (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000))
    & (df.turnover_rate.between(1.5, 8))
    & (df.gap.between(-0.04, -0.01))
    & u
)

for label, cond in [
    ("none", pd.Series(True, index=df.index)),
    ("<0.1", df.close_pos < 0.1),
    ("<0.2", df.close_pos < 0.2),
    ("<0.3", df.close_pos < 0.3),
    ("<0.4", df.close_pos < 0.4),
    ("<0.5", df.close_pos < 0.5),
    (">0.5", df.close_pos > 0.5),
    ("yin", df.close < df.open),
]:
    for rank in (["rank_gap"] if label != "none" else ["rank_gap", "rank_close_pos"]):
        d = df[base & cond].dropna(subset=["r_oc2"]).sort_values(["trade_date", rank])
        picks = d.groupby("trade_date").head(5)
        if len(picks) < 20:
            continue
        daily = picks.groupby("trade_date")["r_oc2"].mean() - 0.0025
        eq = (1 + daily).cumprod()
        yr = picks.groupby(picks.trade_date.dt.year)["r_oc2"].agg(["size", "mean"]).round(4)
        print(f"pos{label:6s} rank={rank:15s} n={len(picks):4d} days={len(daily):3d} "
              f"avg={picks.r_oc2.mean()-0.0025:+.4f} tot={eq.iloc[-1]-1:+7.1%} "
              f"dd={(eq/eq.cummax()-1).min():+7.1%} sh={daily.mean()/daily.std()*np.sqrt(252):5.2f}")
        print("   ", yr.to_dict("index"))
