"""Tune diversification / market filter / sizing for the v2 rule (correct mean index)."""
import numpy as np
import pandas as pd

from strategy_lab import load, universe

df = load()
u = universe(df)
mkt = df.groupby("trade_date").agg(mkt=("pct_chg", "mean"), mb20=("b20", "first"))
mkt["m_idx"] = (1 + mkt["mkt"] / 100).cumprod()
mkt["m_ma5"] = mkt["m_idx"].rolling(5).mean()
mkt["m_ma10"] = mkt["m_idx"].rolling(10).mean()
mkt["m_ma20"] = mkt["m_idx"].rolling(20).mean()
mkt["m_ma10_slope"] = mkt["m_ma10"].diff(3)
mkt["m_ret5"] = mkt["m_idx"] / mkt["m_idx"].shift(5) - 1
mcols = ["m_idx", "m_ma5", "m_ma10", "m_ma20", "m_ma10_slope", "m_ret5", "mb20"]
df = df.drop(columns=[c for c in mcols if c in df.columns])
df = df.merge(mkt[mcols], left_on="trade_date", right_index=True, how="left")
df["rank_gap"] = df["gap"]
bench = df[u].dropna(subset=["r_oc2"]).groupby("trade_date")["r_oc2"].mean()

base = (
    (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000))
    & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > 0.03)
    & (df.pct_chg.between(-6, -1.5))
    & (df.gap.between(-0.04, -0.01))
    & (df.turnover_rate.between(1.5, 8))
    & u
)

FILTERS = {
    "none": pd.Series(True, index=df.index),
    "idx>ma10": df.m_idx > df.m_ma10,
    "idx>ma10 & slope>0": (df.m_idx > df.m_ma10) & (df.m_ma10_slope > 0),
    "idx>ma10 & b20>0.3": (df.m_idx > df.m_ma10) & (df.mb20 > 0.3),
    "idx>ma20": df.m_idx > df.m_ma20,
    "idx>ma5": df.m_idx > df.m_ma5,
    "idx>ma10 & ret5>-5%": (df.m_idx > df.m_ma10) & (df.m_ret5 > -0.05),
}

rows = []
for fname, f in FILTERS.items():
    for top_n in (5, 8, 10):
        d = df[base & f].dropna(subset=["r_oc2"]).sort_values(["trade_date", "rank_gap"])
        picks = d.groupby("trade_date").head(top_n)
        daily = picks.groupby("trade_date")["r_oc2"].mean() - 0.0025
        if len(daily) < 5:
            continue
        eq = (1 + daily).cumprod()
        dd = (eq / eq.cummax() - 1).min()
        ex = (daily - bench.reindex(daily.index)).dropna()
        row = dict(filt=fname, top=top_n, days=len(daily), n=len(picks),
                   avg=round(picks.r_oc2.mean() - 0.0025, 4),
                   tot=round(eq.iloc[-1] - 1, 3), dd=round(dd, 3),
                   sh=round(daily.mean() / daily.std() * np.sqrt(252), 2),
                   exc=round(ex.mean(), 4),
                   exct=round(ex.mean() / (ex.std() / np.sqrt(len(ex))), 2))
        rows.append(row)
        print(row)
    print()

print(pd.DataFrame(rows).sort_values("sh", ascending=False).to_string(index=False))
