"""Refine the gap-reversal-in-uptrend rule (rule 6) with regime filters and OOS split."""
import numpy as np
import pandas as pd

from strategy_lab import load, universe, COST, metrics, yearly


def run(df, mask, rank, top_n=5, ret="r_oc2", cost=COST):
    d = df[mask].dropna(subset=[ret]).copy()
    d = d.sort_values(["trade_date", rank], ascending=[True, True])
    picks = d.groupby("trade_date").head(top_n)
    daily = picks.groupby("trade_date")[ret].mean() - cost
    return daily, picks


def report(name, daily, picks, bench_daily, ret="r_oc2"):
    m = metrics(daily, picks, ret)
    excess = (daily - bench_daily.reindex(daily.index)).dropna()
    m2 = {
        "exc_mean": round(excess.mean(), 4),
        "exc_t": round(excess.mean() / (excess.std() / np.sqrt(len(excess))), 2) if len(excess) > 3 else np.nan,
        "ir": round(excess.mean() / excess.std() * np.sqrt(252), 2) if excess.std() > 0 else np.nan,
        "exc_total": round((1 + excess).prod() - 1, 4),
    }
    print(f"\n== {name} ==")
    print(m, m2)
    print(yearly(daily).to_string())
    return m, m2


def main():
    df = load()
    df["rank_gap"] = df["gap"]
    u = universe(df)
    # benchmark daily (equal weight universe)
    b = df[u].dropna(subset=["r_oc2"]).groupby("trade_date")["r_oc2"].mean() - COST
    b1 = df[u].dropna(subset=["r_oo1"]).groupby("trade_date")["r_oo1"].mean() - COST

    base = (
        (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000))
        & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > 0.03)
        & (df.pct_chg.between(-6, -1.5))
        & (df.gap.between(-0.04, -0.005))
        & (df.turnover_rate.between(1.5, 8))
        & u
    )
    # market regime filters
    mkt = df.groupby("trade_date").agg(b20=("b20", "first"), med=("pct_chg", "median"))
    mkt["med5"] = mkt["med"].rolling(5).sum()
    mkt["b20_5"] = mkt["b20"].diff(5)
    df = df.merge(mkt[["b20_5"]], left_on="trade_date", right_index=True, how="left")

    print("### 1. hold T+1 open -> T+2 close (2 nights)")
    d, p = run(df, base, "rank_gap", ret="r_oc2")
    report("base_oc2", d, p, b, "r_oc2")

    print("\n### 2. hold T+1 open -> T+2 open (1 night)")
    d1, p1 = run(df, base, "rank_gap", ret="r_oo1")
    report("base_oo1", d1, p1, b1, "r_oo1")

    print("\n### 3. gap range variants (oc2)")
    for lo, hi in [(-0.05, -0.005), (-0.04, -0.005), (-0.03, -0.005), (-0.04, -0.01), (-0.03, -0.01), (-0.02, -0.005), (-0.06, -0.02)]:
        m = base & df.gap.between(lo, hi)
        d, p = run(df, m, "rank_gap")
        ex = (d - b.reindex(d.index)).dropna()
        print(f"gap[{lo},{hi}] n={len(p)} days={len(d)} avg={p.r_oc2.mean()-COST:.4f} "
              f"total={(1+d).prod()-1:.3f} dd={metrics(d)['maxdd']} exc={ex.mean():.4f} exct={ex.mean()/(ex.std()/np.sqrt(len(ex))):.2f}")

    print("\n### 4. trend variants")
    for r20, cond in [(0.0, "close>ma20"), (0.03, "close>ma20,ma20>ma60"), (0.08, "ret20>8%"), (0.03, "ma20>ma60")]:
        if cond == "close>ma20":
            m = base & (df.ret20 > r20) & (df.close > df.ma20)
        elif cond == "ret20>8%":
            m = base & (df.ret20 > 0.08)
        else:
            m = base
        d, p = run(df, m, "rank_gap")
        ex = (d - b.reindex(d.index)).dropna()
        print(f"{cond} r20>{r20}: n={len(p)} avg={p.r_oc2.mean()-COST:.4f} total={(1+d).prod()-1:.3f} "
              f"dd={metrics(d)['maxdd']} exc={ex.mean():.4f} t={ex.mean()/(ex.std()/np.sqrt(len(ex))):.2f}")

    print("\n### 5. market regime filters (b20 / b20 5d change)")
    for label, cond in [
        ("all", pd.Series(True, index=df.index)),
        ("b20>0.25", df.b20 > 0.25),
        ("b20>0.35", df.b20 > 0.35),
        ("b20<0.25", df.b20 < 0.25),
        ("b20_5>-0.05", df.b20_5 > -0.05),
        ("b20_5>0", df.b20_5 > 0),
    ]:
        m = base & cond
        d, p = run(df, m, "rank_gap")
        final = (1 + d).prod() - 1
        ex = (d - b.reindex(d.index)).dropna()
        print(f"{label}: n={len(p)} days={len(d)} avg={p.r_oc2.mean()-COST:.4f} total={final:.3f} "
              f"dd={metrics(d)['maxdd']} sharpe={metrics(d)['sharpe']} exc={ex.mean():.4f}")

    print("\n### 6. rank variants")
    df["rank_gap_desc"] = -df["gap"]
    df["rank_ret20"] = -df["ret20"]
    df["rank_amt"] = -df["amount"]
    for rk in ["rank_gap", "rank_gap_desc", "rank_ret20", "rank_amt"]:
        d, p = run(df, base, rk)
        print(f"{rk}: n={len(p)} avg={p.r_oc2.mean()-COST:.4f} total={(1+d).prod()-1:.3f} dd={metrics(d)['maxdd']}")

    print("\n### 7. cost sensitivity (oc2, base)")
    for c in [0.001, 0.0015, 0.0025, 0.004]:
        d, p = run(df, base, "rank_gap", cost=c)
        print(f"cost={c}: total={(1+d).prod()-1:.3f} ann={metrics(d)['ann']} dd={metrics(d)['maxdd']} avg={p.r_oc2.mean()-c:.4f}")


if __name__ == "__main__":
    main()
