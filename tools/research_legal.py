"""Legal (T+1) holding-pattern research.

A-share T+1: shares bought on day D can only be sold on D+1.
Legal patterns (signal at T close, decisions at T+1 open):
  ON    buy T close, sell T+1 open          -> r_overnight
  D1    buy T close, sell T+1 close         -> r_1d
  OO1   buy T+1 open, sell T+2 open         -> open2/open1 - 1
  OC2   buy T+1 open, sell T+2 close        -> close2/open1 - 1
"""
import numpy as np
import pandas as pd

PANEL = "data/research/panel_cache.parquet"


def load():
    df = pd.read_parquet(PANEL)
    df = df[(df.trade_date >= "2024-09-01") & (df.pct_chg.abs() < 21)].copy()
    g = df.groupby("ts_code", sort=False)
    df["vol_ratio"] = df["amount"] / g["amount"].shift(1).rolling(10).mean().reset_index(level=0, drop=True)
    df["gap"] = df["r_overnight"]
    df["r_oo1"] = df["f_open2"] / df["f_open1"] - 1
    df["r_oc2"] = df["f_close2"] / df["f_open1"] - 1
    df["r_oo3"] = df["f_open3"] / df["f_open1"] - 1
    df["entry_ok"] = df["f_open1"].notna() & (df["f_open1"] > 0)
    # tradability: skip limit-up at T (cannot buy at close), limit-down at T+1 (cannot sell)
    df["can_buy_close"] = (df["pct_chg"] < df["board_limit"] * 100 - 0.3)
    return df


def stats(s: pd.Series) -> dict:
    s = s.dropna()
    if len(s) < 3:
        return {"n": len(s)}
    return {"n": len(s), "mean": s.mean(), "median": s.median(),
            "win": (s > 0).mean(), "std": s.std(),
            "t": s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))}


def by_bucket(df, ret, by, bins=10):
    d = df.dropna(subset=[ret, by]).copy()
    d["b"] = pd.qcut(d[by], bins, duplicates="drop")
    out = d.groupby("b", observed=True)[ret].agg(["size", "mean", "median", lambda s: (s > 0).mean()])
    out.columns = ["n", "mean", "median", "win"]
    out["t"] = out["mean"] / (d.groupby("b", observed=True)[ret].std() / np.sqrt(out["n"]))
    return out.round(4)


def main():
    df = load()
    print("== legal patterns, unconditional ==")
    for r in ["r_overnight", "r_1d", "r_oo1", "r_oc2", "r_oo3"]:
        print(r, {k: round(v, 4) for k, v in stats(df[r]).items()})

    print("\n== OO1 by T+1 gap ==")
    d = df.copy()
    d["gapb"] = pd.cut(d["gap"], [-0.2, -0.05, -0.04, -0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03, 0.05, 0.2])
    print(d.groupby("gapb", observed=True)["r_oo1"].agg(["size", "mean", "median", lambda s: (s > 0).mean()]).round(4))

    print("\n== OC2 by T+1 gap ==")
    print(d.groupby("gapb", observed=True)["r_oc2"].agg(["size", "mean", "median", lambda s: (s > 0).mean()]).round(4))

    print("\n== OO1 by prior-day pct ==")
    d["pctb"] = pd.cut(d["pct_chg"], [-21, -7, -4, -2, 0, 2, 4, 7, 9.7, 21])
    print(d.groupby("pctb", observed=True)["r_oo1"].agg(["size", "mean", "median", lambda s: (s > 0).mean()]).round(4))

    print("\n== D1 by prior-day pct ==")
    print(d.groupby("pctb", observed=True)["r_1d"].agg(["size", "mean", "median", lambda s: (s > 0).mean()]).round(4))

    print("\n== OO1 by turnover ==")
    print(by_bucket(d, "r_oo1", "turnover_rate"))
    print("\n== OO1 by b20 ==")
    print(by_bucket(d, "r_oo1", "b20"))

    # 8step candidates, legal patterns
    m = (
        (((df.pct_chg >= 3) & (df.pct_chg <= 6)) | ((df.pct_chg >= 6) & (df.pct_chg <= 9.7)))
        & (df.amount >= 0.5e8) & (df.amount <= 50e8)
        & (df.turnover_rate >= 3) & (df.turnover_rate <= 10)
        & (df.close > df.ma5) & (df.ma5 > df.ma10)
        & (df.dist_high20 > -0.08)
    )
    sub = df[m]
    print("\n== 8step (no cap filter) legal patterns ==")
    for r in ["r_overnight", "r_1d", "r_oo1", "r_oc2"]:
        print(r, {k: round(v, 4) for k, v in stats(sub[r]).items()})
    print("by gap, OO1:")
    print(sub.assign(gapb=pd.cut(sub["gap"], [-0.2, -0.05, -0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.05, 0.2]))
          .groupby("gapb", observed=True)["r_oo1"].agg(["size", "mean", "median", lambda s: (s > 0).mean()]).round(4))


if __name__ == "__main__":
    main()
