"""方案 A：A 级通过者等权组合回测（43 笔随机样本）+ 压力测试。"""
import json

import numpy as np
import pandas as pd

OUT = "docs/overnight_v2"
COST = 0.0025


def portfolio(d, label):
    daily = d.groupby("date").net.mean()
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    years = max((daily.index.max() - daily.index.min()).days / 365.25, 0.1)
    rng = np.random.default_rng(42)
    boots = [daily.sample(len(daily), replace=True, random_state=int(rng.integers(1e9))).mean()
             for _ in range(3000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    n_day = d.groupby("date").size()
    return {
        "label": label, "trades": int(len(d)), "days": int(len(daily)),
        "names_per_day": round(float(n_day.mean()), 2),
        "max_names": int(n_day.max()),
        "win_trade": round(float((d.net > 0).mean()), 4),
        "win_day": round(float((daily > 0).mean()), 4),
        "avg": round(float(d.net.mean()), 4),
        "avg_day": round(float(daily.mean()), 4),
        "total": round(float(eq.iloc[-1] - 1), 4),
        "ann": round(float(eq.iloc[-1] ** (1 / years) - 1), 4),
        "dd": round(float(dd.min()), 4),
        "sharpe": round(float(daily.mean() / daily.std() * np.sqrt(252)), 2),
        "t_day": round(float(daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily)))), 2),
        "boot_lo": round(float(lo), 4), "boot_hi": round(float(hi), 4),
        "boot_pos": round(float(np.mean(np.array(boots) > 0)), 3),
        "monthly": [{"ym": f"{y}-{m:02d}", "ret": round(float(v - 1), 4)}
                    for (y, m), v in (1 + daily).groupby([daily.index.year, daily.index.month]).prod().items()],
    }


def main():
    d = pd.read_csv(f"{OUT}/v3_trades.csv")
    d["date"] = pd.to_datetime(d["signal_date"])
    d = d[["date", "ts_code", "net"]].sort_values("date").reset_index(drop=True)
    res = {"plan_a": portfolio(d, "方案 A · 43 笔随机 A 级等权")}

    # 压力测试：剔除最赚钱的 N 笔
    for n in (3, 5, 10):
        drop = d.nlargest(n, "net").index
        res[f"drop_top{n}"] = portfolio(d.drop(drop), f"剔除最赚 {n} 笔")
    # 只看 2025 / 2026
    for y in (2025, 2026):
        res[f"y{y}"] = portfolio(d[d.date.dt.year == y], f"{y} 年")
    # 对比：121 笔 top1 样本
    a = pd.read_csv(f"{OUT}/v3_1_a_trades.csv")
    a["date"] = pd.to_datetime(a["date"])
    res["top1_121"] = portfolio(a[["date", "code", "net"]].rename(columns={"code": "ts_code"}),
                                "对比：121 天 top1 A 级")

    with open(f"{OUT}/plan_a_backtest.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    for k, x in res.items():
        print(f"{k}: n={x['trades']} days={x['days']} names/day={x['names_per_day']} "
              f"winT={x['win_trade']:.1%} winD={x['win_day']:.1%} avg={x['avg']:+.2%} "
              f"ann={x['ann']:+.1%} dd={x['dd']:+.1%} t={x['t_day']} "
              f"CI=[{x['boot_lo']:+.2%},{x['boot_hi']:+.2%}]")


if __name__ == "__main__":
    main()
