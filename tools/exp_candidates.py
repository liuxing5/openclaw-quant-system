"""Evaluate named candidate configurations with conservative costs and OOS split."""
import numpy as np
import pandas as pd

from strategy_lab import load, universe

df = load()
df["rank_gap"] = df["gap"]
u = universe(df)
mkt = df.groupby("trade_date").agg(mkt=("pct_chg", "median"), mb20=("b20", "first"))
mkt["idx"] = (1 + mkt["mkt"] / 100).cumprod()
mkt["idx_ma10"] = mkt["idx"].rolling(10).mean()
mkt["idx_ma20"] = mkt["idx"].rolling(20).mean()
mkt["idx_ret5"] = mkt["idx"] / mkt["idx"].shift(5) - 1
df = df.drop(columns=[c for c in ["idx", "idx_ma10", "idx_ma20", "idx_ret5"] if c in df.columns])
df = df.merge(mkt[["idx", "idx_ma10", "idx_ma20", "idx_ret5", "mb20"]], left_on="trade_date", right_index=True, how="left")
df["rank_gap"] = df["gap"]

TH = pd.Timestamp("2025-10-01")
bench = df[u].dropna(subset=["r_oc2"]).groupby("trade_date")["r_oc2"].mean()

CFG = {
    "A_simple": dict(gap=(-0.04, -0.01), pb=(-6, -1.5), r20=0.03, filt=None),
    "B_idx10": dict(gap=(-0.04, -0.01), pb=(-6, -1.5), r20=0.03, filt="idx10"),
    "C_wide_idx10": dict(gap=(-0.05, -0.015), pb=(-8, -2), r20=0.0, filt="idx10"),
    "D_idx20": dict(gap=(-0.04, -0.01), pb=(-6, -1.5), r20=0.03, filt="idx20"),
    "E_b20": dict(gap=(-0.04, -0.01), pb=(-6, -1.5), r20=0.03, filt="b20"),
    "F_idx10_b20": dict(gap=(-0.04, -0.01), pb=(-6, -1.5), r20=0.03, filt="idx10_b20"),
}

FILT = {
    None: pd.Series(True, index=df.index),
    "idx10": df.idx > df.idx_ma10,
    "idx20": df.idx > df.idx_ma20,
    "b20": df.mb20 >= 0.30,
    "idx10_b20": (df.idx > df.idx_ma10) & (df.mb20 >= 0.30),
}


def stats(daily, picks, cost):
    eq = (1 + daily).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    years = (daily.index.max() - daily.index.min()).days / 365.25
    return {
        "days": len(daily), "n": len(picks), "per_day": round(len(picks) / len(daily), 2),
        "avg": round(picks.r_oc2.mean() - cost, 4),
        "win": round((picks.r_oc2 > cost).mean(), 3),
        "total": round(eq.iloc[-1] - 1, 3),
        "ann": round(eq.iloc[-1] ** (1 / years) - 1, 3),
        "dd": round(dd, 3),
        "sharpe": round(daily.mean() / daily.std() * np.sqrt(252), 2),
        "excess": round((daily - bench.reindex(daily.index)).mean() - cost, 4),
    }


for cost in (0.0015, 0.0025):
    print(f"\n################ cost = {cost:.2%} ################")
    for name, c in CFG.items():
        m = (
            (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000))
            & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > c["r20"])
            & (df.pct_chg.between(*c["pb"]))
            & (df.gap.between(*c["gap"]))
            & (df.turnover_rate.between(1.5, 8))
            & u & FILT[c["filt"]]
        )
        d = df[m].dropna(subset=["r_oc2"]).sort_values(["trade_date", "rank_gap"])
        picks = d.groupby("trade_date").head(5)
        daily = picks.groupby("trade_date")["r_oc2"].mean() - cost
        tr = daily[daily.index < TH]
        te = daily[daily.index >= TH]
        ptr = picks[picks.trade_date < TH]
        pte = picks[picks.trade_date >= TH]
        a = stats(daily, picks, cost)
        print(f"{name:14s} {a}")
        print(f"{'':14s} train n={len(ptr)} avg={ptr.r_oc2.mean()-cost:+.4f} tot={(1+tr).prod()-1:+.1%} dd={(lambda e:(e/e.cummax()-1).min())((1+tr).cumprod()):+.1%}"
              f" | test n={len(pte)} avg={pte.r_oc2.mean()-cost:+.4f} tot={(1+te).prod()-1:+.1%} dd={(lambda e:(e/e.cummax()-1).min())((1+te).cumprod()):+.1%}")

# monthly for A and B
print("\n== monthly returns ==")
for name in ["A_simple", "B_idx10", "C_wide_idx10"]:
    c = CFG[name]
    m = (
        (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000))
        & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > c["r20"])
        & (df.pct_chg.between(*c["pb"])) & (df.gap.between(*c["gap"]))
        & (df.turnover_rate.between(1.5, 8)) & u & FILT[c["filt"]]
    )
    d = df[m].dropna(subset=["r_oc2"]).sort_values(["trade_date", "rank_gap"])
    picks = d.groupby("trade_date").head(5)
    daily = picks.groupby("trade_date")["r_oc2"].mean() - 0.0025
    mon = (1 + daily).groupby([daily.index.year, daily.index.month]).prod() - 1
    print(f"\n{name}:")
    print(mon.round(3).to_string())
