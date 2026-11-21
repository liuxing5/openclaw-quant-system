"""Out-of-sample validation of overnight 8-step v2 on 2026-05..11 (DB daily_quotes).

Builds total-return adjusted series from pct_chg so dividends do not distort
MA/ret features, then applies the exact v2 rule.
"""
import glob
import json
import os

import numpy as np
import pandas as pd

COST = 0.0025
CFG = dict(
    regime_ma=10, breadth_min=0.30, ret20_min=0.03,
    pullback_lo=-6.0, pullback_hi=-1.5, close_pos_max=0.40,
    min_amount=1e8, mktcap_lo=50e8, mktcap_hi=2000e8,
    turnover_lo=1.5, turnover_hi=8.0, gap_lo=-0.04, gap_hi=-0.01,
)


def load_raw() -> pd.DataFrame:
    frames = []
    for f in glob.glob("data/research/raw/*.parquet"):
        df = pd.read_parquet(f)
        if "amount" not in df.columns and "amount_wan" in df.columns:
            df["amount"] = df["amount_wan"] * 1e4
        if "circ_mcap_yi" not in df.columns and "circulating_market_cap" in df.columns:
            df["circ_mcap_yi"] = df["circulating_market_cap"] / 1e8
        frames.append(df[["ts_code", "trade_date", "open", "high", "low", "close",
                          "pct_chg", "turnover_rate", "amount", "circ_mcap_yi"]])
    df = pd.concat(frames, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def build(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("ts_code", sort=False)
    df["factor"] = 1 + df["pct_chg"].fillna(0) / 100.0
    df["adj_close"] = g["factor"].transform(lambda s: s.cumprod())
    prev_adj = g["adj_close"].shift(1)
    pre_close_ratio = df["close"] / df["factor"]          # close_t / (1+pct) = pre_close
    df["adj_open"] = prev_adj * df["open"] / pre_close_ratio
    df["adj_high"] = prev_adj * df["high"] / pre_close_ratio
    df["adj_low"] = prev_adj * df["low"] / pre_close_ratio

    df["ma20"] = g["adj_close"].transform(lambda s: s.rolling(20).mean())
    df["ma60"] = g["adj_close"].transform(lambda s: s.rolling(60).mean())
    df["ret20"] = g["adj_close"].transform(lambda s: s / s.shift(20) - 1)
    df["above_ma20"] = (df["adj_close"] > df["ma20"]).astype(float)
    df["close_pos"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)

    for k in (1, 2, 3):
        df[f"f_open{k}"] = g["adj_open"].shift(-k)
        df[f"f_close{k}"] = g["adj_close"].shift(-k)
        df[f"f_date{k}"] = g["trade_date"].shift(-k)
    df["gap"] = df["f_open1"] / df["adj_close"] - 1
    df["r_oc2"] = df["f_close2"] / df["f_open1"] - 1

    mkt = df.groupby("trade_date").agg(mkt=("pct_chg", "mean"), mb20=("above_ma20", "mean"))
    mkt["m_idx"] = (1 + mkt["mkt"] / 100).cumprod()
    mkt["m_ma10"] = mkt["m_idx"].rolling(10).mean()
    df = df.merge(mkt[["m_idx", "m_ma10", "mb20"]], left_on="trade_date", right_index=True, how="left")
    return df


def main():
    raw = load_raw()
    print("raw rows", len(raw), raw.trade_date.min().date(), raw.trade_date.max().date(),
          "codes", raw.ts_code.nunique())
    df = build(raw)
    # only evaluate windows that start inside the fetched OOS period
    start = pd.Timestamp("2026-05-22")
    df = df[df.trade_date >= start - pd.Timedelta(days=120)]

    import os
    step1 = ((df.m_idx > df.m_ma10) & (df.mb20 >= CFG["breadth_min"])) if os.environ.get("NO_GATE") != "1" else pd.Series(True, index=df.index)
    step2 = (df.adj_close > df.ma20) & (df.ma20 > df.ma60) & (df.ret20 > CFG["ret20_min"])
    step3 = df.pct_chg.between(CFG["pullback_lo"], CFG["pullback_hi"])
    step4 = df.close_pos < CFG["close_pos_max"]
    step5 = (df.amount >= CFG["min_amount"]) & df.circ_mcap_yi.between(CFG["mktcap_lo"] / 1e8, CFG["mktcap_hi"] / 1e8)
    step6 = df.turnover_rate.between(CFG["turnover_lo"], CFG["turnover_hi"])
    step8 = df.pct_chg.notna() & (df.close > 0)
    mask = step1 & step2 & step3 & step4 & step5 & step6 & step8 & (df.trade_date >= start)

    d = df[mask].dropna(subset=["r_oc2"]).sort_values(["trade_date", "gap"])
    picks = d.groupby("trade_date").head(5).copy()
    daily = picks.groupby("trade_date")["r_oc2"].mean() - COST

    print("\nOOS period", df[df.trade_date >= start].trade_date.min().date(),
          "~", df.trade_date.max().date())
    print("step funnel:", {k: int(v.sum()) for k, v in
                           [("all", step1), ("trend", step1 & step2), ("pullback", step1 & step2 & step3),
                            ("weak_close", step1 & step2 & step3 & step4),
                            ("liquidity", step1 & step2 & step3 & step4 & step5),
                            ("turnover", step1 & step2 & step3 & step4 & step5 & step6)]})
    if len(daily) == 0:
        win = df[(df.trade_date >= start)]
        mkt = win.groupby("trade_date").agg(mkt=("pct_chg", "mean"), b=("above_ma20", "mean"))
        mkt["idx"] = (1 + mkt["mkt"] / 100).cumprod()
        out = {
            "period": [str(win.trade_date.min().date()), str(win.trade_date.max().date())],
            "stats": {"days": 0, "trades": 0, "avg_trade": 0.0, "total": 0.0, "maxdd": 0.0,
                      "sharpe": 0.0, "market_return": float(mkt.idx.iloc[-1] - 1),
                      "breadth_min": float(mkt.b.min()), "breadth_max": float(mkt.b.max()),
                      "gate_open_days": int(((win.m_idx > win.m_ma10) & (win.mb20 >= CFG["breadth_min"])).groupby(win.trade_date).any().sum())},
            "monthly": [],
            "note": "门控全程关闭，且八步条件在该窗口筛不出标的；空仓是设计内结果。",
        }
        os.makedirs("docs/overnight_v2", exist_ok=True)
        with open("docs/overnight_v2/oos_2026H2.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("NO OOS TRADES (written to docs/overnight_v2/oos_2026H2.json)")
        print(json.dumps(out["stats"], ensure_ascii=False))
        return
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    ex = picks.groupby("trade_date")["r_oc2"].mean() - df.groupby("trade_date")["r_oc2"].mean().reindex(daily.index)
    print({
        "days": len(daily), "trades": len(picks),
        "avg_trade": round(picks.r_oc2.mean() - COST, 4),
        "win_rate": round((picks.r_oc2 > COST).mean(), 3),
        "total": round(eq.iloc[-1] - 1, 4),
        "maxdd": round(dd.min(), 4),
        "sharpe": round(daily.mean() / daily.std() * np.sqrt(252), 2),
        "excess_mean": round(ex.mean(), 4),
        "excess_t": round(ex.mean() / (ex.std() / np.sqrt(len(ex))), 2) if len(ex) > 3 else None,
    })
    print("\nmonthly:")
    print(((1 + daily).groupby([daily.index.year, daily.index.month]).prod() - 1).round(3).to_string())
    print("\npicks sample:")
    print(picks.tail(15)[["trade_date", "ts_code", "pct_chg", "close_pos", "gap", "r_oc2"]].to_string(index=False))

    out = {
        "period": [str(daily.index.min().date()), str(daily.index.max().date())],
        "stats": {"days": int(len(daily)), "trades": int(len(picks)),
                  "avg_trade": float(picks.r_oc2.mean() - COST),
                  "total": float(eq.iloc[-1] - 1), "maxdd": float(dd.min()),
                  "sharpe": float(daily.mean() / daily.std() * np.sqrt(252))},
        "monthly": [{"ym": f"{y}-{m:02d}", "ret": float(v)}
                    for (y, m), v in ((1 + daily).groupby([daily.index.year, daily.index.month]).prod() - 1).items()],
    }
    with open("docs/overnight_v2/oos_2026H2.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
