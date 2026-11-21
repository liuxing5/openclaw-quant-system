"""Grid search for a robust gap-reversal overnight-watch strategy (train/test split)."""
import itertools
import numpy as np
import pandas as pd

from strategy_lab import load, universe, COST

df = load()
df["rank_gap"] = df["gap"]
u = universe(df)
mkt = df.groupby("trade_date").agg(mkt=("pct_chg", "median"), mb20=("b20", "first"))
mkt["idx"] = (1 + mkt["mkt"] / 100).cumprod()
mkt["idx_ma10"] = mkt["idx"].rolling(10).mean()
mkt["idx_ma20"] = mkt["idx"].rolling(20).mean()
mkt["idx_ret5"] = mkt["idx"] / mkt["idx"].shift(5) - 1
mkt["idx_ret3"] = mkt["idx"] / mkt["idx"].shift(3) - 1
df = df.drop(columns=[c for c in ["idx", "idx_ma10", "idx_ma20", "idx_ret5", "idx_ret3"] if c in df.columns])
df = df.merge(mkt[["idx", "idx_ma10", "idx_ma20", "idx_ret5", "idx_ret3", "mb20"]], left_on="trade_date", right_index=True, how="left")
df["rank_gap"] = df["gap"]

bench = df[u].dropna(subset=["r_oc2"]).groupby("trade_date")["r_oc2"].mean() - COST
TRAIN_END = pd.Timestamp("2025-10-01")

filters = {
    "none": pd.Series(True, index=df.index),
    "idx>ma20": df.idx > df.idx_ma20,
    "idx>ma10": df.idx > df.idx_ma10,
    "b20>=0.30": df.mb20 >= 0.30,
    "idx5d>-4%": df.idx_ret5 > -0.04,
    "idx5d>-6%": df.idx_ret5 > -0.06,
    "idx3d>-3%": df.idx_ret3 > -0.03,
}

rows = []
for gap_lo, gap_hi in [(-0.04, -0.01), (-0.05, -0.015), (-0.035, -0.008), (-0.045, -0.008), (-0.03, -0.008)]:
    for pb_lo, pb_hi in [(-6, -1.5), (-5, -1), (-8, -2)]:
        for r20 in [0.0, 0.03, 0.08]:
            for fname, fcond in filters.items():
                m = (
                    (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000))
                    & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > r20)
                    & (df.pct_chg.between(pb_lo, pb_hi))
                    & (df.gap.between(gap_lo, gap_hi))
                    & (df.turnover_rate.between(1.5, 8))
                    & u & fcond
                )
                d = df[m].dropna(subset=["r_oc2"]).sort_values(["trade_date", "rank_gap"])
                picks = d.groupby("trade_date").head(5)
                daily = picks.groupby("trade_date")["r_oc2"].mean() - COST
                if len(daily) < 30:
                    continue
                tr = daily[daily.index < TRAIN_END]
                te = daily[daily.index >= TRAIN_END]
                if len(tr) < 20 or len(te) < 20:
                    continue

                def sharpe(x):
                    return x.mean() / x.std() * np.sqrt(252) if x.std() > 0 else np.nan

                def dd(x):
                    e = (1 + x).cumprod()
                    return (e / e.cummax() - 1).min()

                rows.append({
                    "gap": f"{gap_lo:.3f}/{gap_hi:.3f}", "pb": f"{pb_lo}/{pb_hi}", "r20": r20,
                    "filt": fname, "n": len(picks),
                    "tr_avg": picks[picks.trade_date < TRAIN_END].r_oc2.mean() - COST,
                    "tr_sh": sharpe(tr), "tr_dd": dd(tr), "tr_tot": (1 + tr).prod() - 1,
                    "te_avg": picks[picks.trade_date >= TRAIN_END].r_oc2.mean() - COST,
                    "te_sh": sharpe(te), "te_dd": dd(te), "te_tot": (1 + te).prod() - 1,
                    "te_n": len(picks[picks.trade_date >= TRAIN_END]),
                })
out = pd.DataFrame(rows)
out = out[(out.tr_avg > 0) & (out.te_avg > 0)]
out["score"] = out[["tr_sh", "te_sh"]].min(axis=1) - out[["tr_dd", "te_dd"]].abs().max(axis=1) * 1.0
pd.set_option("display.width", 250)
pd.set_option("display.max_rows", 80)
print(out.sort_values("score", ascending=False).head(40).round(4).to_string(index=False))
out.to_csv("data/research/grid_gap_reversal.csv", index=False)
