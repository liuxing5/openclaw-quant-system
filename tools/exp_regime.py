"""Regime filters, hold periods and drawdown control for the gap-reversal rule."""
import numpy as np
import pandas as pd

from strategy_lab import load, universe, COST, metrics, yearly


def add_market(df):
    m = df.groupby("trade_date").agg(mkt=("pct_chg", "median"), m_b20=("b20", "first"))
    m["idx"] = (1 + m["mkt"] / 100).cumprod()
    m["idx_ma20"] = m["idx"].rolling(20).mean()
    m["idx_ma60"] = m["idx"].rolling(60).mean()
    m["idx_ret5"] = m["idx"] / m["idx"].shift(5) - 1
    m["idx_ret20"] = m["idx"] / m["idx"].shift(20) - 1
    m["m_b20_chg5"] = m["m_b20"].diff(5)
    cols = m.drop(columns=["mkt"])
    return df.drop(columns=[c for c in cols.columns if c in df.columns], errors="ignore").merge(
        cols, left_on="trade_date", right_index=True, how="left")


def run(df, mask, rank="rank_gap", top_n=5, ret="r_oc2", cost=COST):
    d = df[mask].dropna(subset=[ret]).copy()
    d = d.sort_values(["trade_date", rank], ascending=[True, True])
    picks = d.groupby("trade_date").head(top_n)
    daily = picks.groupby("trade_date")[ret].mean() - cost
    return daily, picks


def show(name, daily, picks, bench, ret="r_oc2"):
    ex = (daily - bench.reindex(daily.index)).dropna()
    eq = (1 + daily).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    yr = yearly(daily)
    print(f"{name:42s} d={len(daily):3d} n={len(picks):5d} avg={picks[ret].mean()-COST:+.4f} "
          f"tot={eq.iloc[-1]-1:+7.1%} dd={dd:+7.1%} sh={daily.mean()/daily.std()*np.sqrt(252):5.2f} "
          f"exc={ex.mean():+.4f} t={ex.mean()/(ex.std()/np.sqrt(len(ex))):5.2f}")
    return yr


def main():
    df = add_market(load())
    df["rank_gap"] = df["gap"]
    u = universe(df)
    bench = df[u].dropna(subset=["r_oc2"]).groupby("trade_date")["r_oc2"].mean() - COST

    base = (
        (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000))
        & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > 0.03)
        & (df.pct_chg.between(-6, -1.5))
        & (df.gap.between(-0.04, -0.01))
        & (df.turnover_rate.between(1.5, 8))
        & u
    )
    print("== baseline variants ==")
    show("base oc2", *run(df, base), bench)
    for ret in ["r_oc2", "r_oc3"]:
        show(f"base {ret}", *run(df, base, ret=ret), bench, ret)

    print("\n== index filters ==")
    filters = {
        "idx>ma20": df.idx > df.idx_ma20,
        "idx>ma20 & ma20>ma60": (df.idx > df.idx_ma20) & (df.idx_ma20 > df.idx_ma60),
        "idx<ma20 (contrarian)": df.idx < df.idx_ma20,
        "idx5d>-2%": df.idx_ret5 > -0.02,
        "idx5d>-4%": df.idx_ret5 > -0.04,
        "b20>=0.35": df.m_b20 >= 0.35,
        "b20_chg5>-0.03": df.m_b20_chg5 > -0.03,
        "idx>ma20 & b20>=0.3": (df.idx > df.idx_ma20) & (df.m_b20 >= 0.3),
    }
    for k, cond in filters.items():
        show(k, *run(df, base & cond), bench)

    print("\n== entry timing ==")
    # buy at T close instead of T+1 open, sell T+2 close -> need r? use close1->close2
    df["r_c1c2"] = df["f_close2"] / df["f_close1"] - 1
    df["r_c1c3"] = df["f_close3"] / df["f_close1"] - 1
    show("buy T close -> T+2 close", *run(df, base, ret="r_c1c2"), bench, "r_c1c2")

    print("\n== pullback depth variants ==")
    for lo, hi in [(-4, -1.5), (-6, -2), (-8, -3), (-5, 0)]:
        m = base & df.pct_chg.between(lo, hi)
        show(f"pct[{lo},{hi}]", *run(df, m), bench)

    print("\n== trend strength variants ==")
    for r20 in [0.0, 0.03, 0.08, 0.15]:
        m = base & (df.ret20 > r20)
        show(f"ret20>{r20}", *run(df, m), bench)

    print("\n== half-year breakdown of base ==")
    d, p = run(df, base)
    print(yearly(d).to_string())
    # worst 10 days
    print("\nworst days:")
    print(d.nsmallest(10).round(4).to_string())
    print("\nbest days:")
    print(d.nlargest(5).round(4).to_string())
    # drawdown periods
    eq = (1 + d).cumprod()
    dd = eq / eq.cummax() - 1
    print("\nmax dd date", dd.idxmin(), "dd", dd.min())
    print("equity around max dd:")
    start = dd.idxmin() - pd.Timedelta(days=15)
    print(eq.loc[start:dd.idxmin() + pd.Timedelta(days=5)].round(3).to_string())


if __name__ == "__main__":
    main()

