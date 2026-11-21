"""每日一笔实测报告：244 天 / 真实 14:30 合格子集 121 天。"""
import json
import pickle

import numpy as np
import pandas as pd

COST = 0.0025
OUT = "docs/overnight_v2"


def stats(daily, trades):
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    years = (daily.index.max() - daily.index.min()).days / 365.25
    return {
        "trades": int(len(trades)), "days": int(len(daily)),
        "win": round(float((trades.net > 0).mean()), 4),
        "avg": round(float(trades.net.mean()), 4),
        "total": round(float(eq.iloc[-1] - 1), 4),
        "ann": round(float(eq.iloc[-1] ** (1 / years) - 1), 4),
        "dd": round(float(dd.min()), 4),
        "sharpe": round(float(daily.mean() / daily.std() * np.sqrt(252)), 2),
    }


def main():
    d = pd.read_csv(f"{OUT}/daily_pick_trades.csv")
    d["date"] = pd.to_datetime(d.date)
    daily = d.groupby("date").net.mean()
    out = {"all": stats(daily, d)}
    out["by_tier"] = {k: stats(g.set_index("date").net, g) for k, g in d.groupby("tier")}
    out["by_exit"] = {k: {"n": int(len(g)), "mean": round(float(g.net.mean()), 4),
                          "win": round(float((g.net > 0).mean()), 3)}
                      for k, g in d.groupby("why")}
    out["monthly"] = [{"ym": f"{y}-{m:02d}", "ret": round(float(v), 4)}
                      for (y, m), v in (1 + daily).groupby(
                          [daily.index.year, daily.index.month]).prod().items()]
    out["trades"] = [{"date": str(r.date.date()), "code": r.code, "tier": r.tier,
                      "entry": r.entry, "sell_time": r.sell_time, "sell": r.sell,
                      "why": r.why, "net": r.net}
                     for r in d.itertuples()]
    with open(f"{OUT}/daily_pick_backtest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out["all"], ensure_ascii=False))
    print("by_tier", out["by_tier"])
    print("by_exit", out["by_exit"])


if __name__ == "__main__":
    main()
