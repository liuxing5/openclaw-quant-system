"""Faithful 'overnight' variant: buy T close, sell T+1 (can stop intraday)."""
import numpy as np
import pandas as pd

from strategy_lab import load, universe, COST, metrics, yearly


def main():
    df = load()
    u = universe(df)
    # T+1 exit path data
    df["t1_open"] = df["f_open1"]
    df["t1_high"] = df["f_high1"]
    df["t1_low"] = df["f_low1"]
    df["t1_close"] = df["f_close1"]
    df["r_t1"] = df["t1_close"] / df["close"] - 1

    bench = df[u].dropna(subset=["r_t1"]).groupby("trade_date")["r_t1"].mean() - COST

    pullback = (
        (df.amount >= 1e8) & (df.circ_mcap_yi.between(50, 2000))
        & (df.close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > 0.03)
        & (df.pct_chg.between(-6, -1.5))
        & (df.turnover_rate.between(1.5, 8))
        & (df.close_pos > 0.3)
        & (df.pct_chg < df.board_limit * 100 - 0.3)
        & u
    )

    def sim(mask, stop=None, target=None, rank="pct_chg", top_n=5):
        d = df[mask].dropna(subset=["r_t1"]).copy()
        d = d.sort_values(["trade_date", rank], ascending=[True, True]).groupby("trade_date").head(top_n)
        entry = d["close"]
        # path: gap at T+1 open, then low/high, then close
        stop_px = entry * (1 + stop) if stop is not None else None
        tgt_px = entry * (1 + target) if target is not None else None
        ret = d["r_t1"].copy()
        if stop is not None:
            gapped = d["t1_open"] <= stop_px
            hit = (~gapped) & (d["t1_low"] <= stop_px)
            ret = np.where(gapped, d["t1_open"] / entry - 1,
                           np.where(hit, stop, ret))
        if target is not None:
            # target checked after stop (conservative: stop first if both)
            gapped_t = (~d["t1_open"].le(stop_px if stop is not None else -1e9)) if stop is not None else pd.Series(True, index=d.index)
            hit_t = (d["t1_high"] >= tgt_px) & gapped_t
            if stop is not None:
                stopped = (d["t1_open"] <= stop_px) | (d["t1_low"] <= stop_px)
                hit_t = hit_t & (~stopped)
            ret = np.where(hit_t, target, ret)
        d = d.assign(ret=ret)
        daily = d.groupby("trade_date")["ret"].mean() - COST
        return daily, d

    def show(name, daily, picks):
        ex = (daily - bench.reindex(daily.index)).dropna()
        eq = (1 + daily).cumprod()
        dd = (eq / eq.cummax() - 1).min()
        print(f"{name:38s} d={len(daily):3d} n={len(picks):5d} avg={picks.ret.mean()-COST:+.4f} "
              f"tot={eq.iloc[-1]-1:+7.1%} dd={dd:+7.1%} sh={daily.mean()/daily.std()*np.sqrt(252):5.2f} "
              f"exc={ex.mean():+.4f} t={ex.mean()/(ex.std()/np.sqrt(len(ex))):5.2f}")
        return eq

    print("== buy T close, sell T+1 close ==")
    show("no stop", *sim(pullback))
    for s in [-0.02, -0.03, -0.04, -0.05]:
        show(f"stop {s:.0%}", *sim(pullback, stop=s))
    for s, t in [(-0.03, 0.04), (-0.03, 0.03), (-0.04, 0.06), (-0.02, 0.03)]:
        show(f"stop {s:.0%} target {t:.0%}", *sim(pullback, stop=s, target=t))

    print("\n== buy T close, sell T+1 open (pure overnight) ==")
    df["r_t1o"] = df["t1_open"] / df["close"] - 1
    bench_o = df[u].dropna(subset=["r_t1o"]).groupby("trade_date")["r_t1o"].mean() - COST
    d = df[pullback].dropna(subset=["r_t1o"]).copy()
    daily = d.groupby("trade_date")["r_t1o"].mean() - COST
    ex = (daily - bench_o.reindex(daily.index)).dropna()
    eq = (1 + daily).cumprod()
    print(f"overnight only: d={len(daily)} avg={d.r_t1o.mean()-COST:+.4f} tot={eq.iloc[-1]-1:+.1%} "
          f"dd={(eq/eq.cummax()-1).min():+.1%} exc={ex.mean():+.4f}")

    print("\n== yearly of stop -3% ==")
    daily, picks = sim(pullback, stop=-0.03)
    print(yearly(daily).to_string())


if __name__ == "__main__":
    main()
