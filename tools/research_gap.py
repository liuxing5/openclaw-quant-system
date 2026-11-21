"""Follow-up research: gap reversal / intraday drift, and 8-step candidate quality."""
import numpy as np
import pandas as pd

PANEL = "data/research/panel_cache.parquet"


def load():
    df = pd.read_parquet(PANEL)
    df = df[df["trade_date"] >= "2024-09-01"].copy()
    df["vol_ratio"] = df["amount"] / df.groupby("ts_code")["amount"].shift(1).rolling(10).mean().reset_index(level=0, drop=True)
    df["gap"] = df["r_overnight"]
    return df


def q(df, col, by, bins=10):
    d = df.dropna(subset=[col, by]).copy()
    d["bucket"] = pd.qcut(d[by], bins, duplicates="drop")
    out = d.groupby("bucket", observed=True).agg(
        n=(col, "size"), mean=(col, "mean"), median=(col, "median"),
        win=(col, lambda s: (s > 0).mean()), std=(col, "std"))
    out["t"] = out["mean"] / (out["std"] / np.sqrt(out["n"]))
    return out.round(4)


def main():
    df = load()
    print("== next-day intraday (open->close) by overnight gap decile ==")
    print(q(df, "r_next_intraday", "gap"))
    print("\n== next-day intraday by gap bucket (fixed) ==")
    df["gapb"] = pd.cut(df["gap"], [-0.2, -0.05, -0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03, 0.05, 0.2])
    print(df.groupby("gapb", observed=True).agg(
        n=("r_next_intraday", "size"), mean=("r_next_intraday", "mean"),
        median=("r_next_intraday", "median"), win=("r_next_intraday", lambda s: (s > 0).mean())).round(4))

    # 8-step-like signal on T, executed T+1
    m = (
        (((df.pct_chg >= 3) & (df.pct_chg <= 6)) | ((df.pct_chg >= 6) & (df.pct_chg <= 9.7)))
        & (df.amount >= 0.5e8) & (df.amount <= 50e8)
        & (df.turnover_rate >= 5) & (df.turnover_rate <= 10)
        & (df.circ_mcap_yi >= 100) & (df.circ_mcap_yi <= 2000)
        & (df.vol_ratio >= 1.5) & (df.vol_ratio <= 8)
        & (df.close > df.ma5) & (df.ma5 > df.ma10)
        & (df.dist_high20 > -0.08)
    )
    sub = df[m].copy()
    print("\n== 8step-like candidates: forward returns ==")
    print(pd.Series({
        "n": len(sub), "days": sub.trade_date.nunique(),
        "overnight": sub.r_overnight.mean(), "next_intraday": sub.r_next_intraday.mean(),
        "next_1d": sub.r_1d.mean(), "next_5d": sub.r_5d.mean(),
    }).round(4))
    print("\n8step candidates: next-day intraday by gap bucket")
    print(sub.groupby("gapb", observed=True).agg(
        n=("r_next_intraday", "size"), mean=("r_next_intraday", "mean"),
        median=("r_next_intraday", "median"), win=("r_next_intraday", lambda s: (s > 0).mean())).round(4))

    # all stocks: intraday by gap + turnover
    print("\n== all: next-day intraday, gap<-1%, by turnover decile ==")
    d = df[df["gap"] < -0.01]
    print(q(d, "r_next_intraday", "turnover_rate"))

    # all stocks: buy at open sell at close, by prior-day pct bucket
    print("\n== all: next-day intraday by prior-day pct bucket ==")
    df["pctb"] = pd.cut(df["pct_chg"], [-0.2, -0.07, -0.04, -0.02, 0, 0.02, 0.04, 0.07, 0.2])
    print(df.groupby("pctb", observed=True).agg(
        n=("r_next_intraday", "size"), mean=("r_next_intraday", "mean"),
        median=("r_next_intraday", "median"), win=("r_next_intraday", lambda s: (s > 0).mean())).round(4))


if __name__ == "__main__":
    main()
