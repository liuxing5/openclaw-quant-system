"""隔夜八步法 v3.0（融合版）详细回测 + 稳健性分析。

14:30 扫描 -> 14:40-15:00 买入 -> 次日 10:00 前卖出。
"""
import json
import os
import pickle

import numpy as np
import pandas as pd

COST = 0.0025
OUT = "docs/overnight_v2"
CACHE = "data/research/morning_exit_bars.pkl"


def simulate(row, bars):
    sig, nxt = str(row.signal_date.date()), row.next_date
    t = [b for b in bars if b["date"] == sig]
    n = [b for b in bars if b["date"] == nxt]
    if not t or not n:
        return None
    entry = next((b for b in t if b["time"] == "1450"), t[-1])["close"]
    by = {b["time"]: b for b in n}
    gap = n[0]["open"] / entry - 1
    if gap >= 0.005:
        return "high_open", gap
    if gap <= -0.03:
        return "panic_open", gap
    stop = entry * 0.98
    for b in n:
        if b["time"] > "1000":
            break
        if b["open"] <= stop:
            return "stop", b["open"] / entry - 1
        if b["low"] <= stop:
            return "stop", -0.02
    last = by.get("1000") or n[-1]
    return "time", last["close"] / entry - 1


def build():
    p = pd.read_parquet("data/research/panic_enriched.parquet")
    base = ((p.pos1430 < 0.2) & p.pct1430.between(-7, -2) & p.turn1430.between(1.5, 8)
            & (p.b20 <= 0.7))
    s = p[base].copy()
    cache = pickle.loads(open(CACHE, "rb").read())
    rows = []
    for r in s.itertuples():
        bars = cache.get((r.ts_code, str(r.signal_date.date()), r.next_date), [])
        if not bars or "volume" not in bars[0]:
            continue
        res = simulate(r, bars)
        if res is None:
            continue
        typ, ret = res
        rows.append({"signal_date": r.signal_date, "ts_code": r.ts_code,
                     "pct1430": r.pct1430, "pos1430": r.pos1430, "vwap_pos": r.vwap_pos,
                     "prev_lu20": r.prev_lu20, "prev_ret5": r.prev_ret5,
                     "amount_ratio": r.amount_ratio_1430, "turn1430": r.turn1430,
                     "b20": r.b20, "gap": r.gap_open, "exit_type": typ,
                     "ret": ret, "net": ret - COST})
    return pd.DataFrame(rows).sort_values("signal_date").reset_index(drop=True)


FILTERS = {
    "vwap": lambda d: d.vwap_pos > -0.02,
    "lu": lambda d: d.prev_lu20 <= 1,
    "ret5": lambda d: d.prev_ret5 > -0.02,
    "amt": lambda d: d.amount_ratio >= 0.8,
}
ALL_DAYS = pd.bdate_range("2025-01-01", "2026-04-21")


def run(d):
    if len(d) < 5:
        return None
    picks = d.groupby("signal_date").head(5)
    active = picks.groupby("signal_date")["net"].mean()
    daily = active.reindex(ALL_DAYS).fillna(0.0)
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    years = (daily.index.max() - daily.index.min()).days / 365.25

    def block(act):
        e = (1 + act).cumprod()
        return {"n_days": int(len(act)), "mean": round(float(act.mean()), 4),
                "win_day": round(float((act > 0).mean()), 3),
                "total": round(float(e.iloc[-1] - 1), 4),
                "dd": round(float((e / e.cummax() - 1).min()), 4)}

    x = d.net
    return {
        "trades": int(len(d)), "active_days": int(d.signal_date.nunique()),
        "avg_net": round(float(x.mean()), 4), "median_net": round(float(x.median()), 4),
        "win": round(float((x > 0).mean()), 4),
        "t": round(float(x.mean() / (x.std() / np.sqrt(len(x)))), 2),
        "total": round(float(eq.iloc[-1] - 1), 4),
        "ann": round(float(eq.iloc[-1] ** (1 / years) - 1), 4),
        "dd": round(float(dd.min()), 4),
        "sharpe": round(float(daily.mean() / daily.std() * np.sqrt(252)), 2),
        "calmar": round(float((eq.iloc[-1] ** (1 / years) - 1) / abs(dd.min())), 2),
        "yearly": {str(y): block(active[active.index.year == y]) for y in [2025, 2026]},
        "quarterly": {f"{y}Q{q}": block(g) for (y, q), g in active.groupby(
            [active.index.year, active.index.quarter])},
        "active": active,
        "eq_series": eq,
        "dd_series": dd,
    }


def main():
    d = build()
    print("base trades", len(d), "days", d.signal_date.nunique())
    mask = pd.Series(True, index=d.index)
    for f in FILTERS.values():
        mask &= f(d)
    core = d[mask].copy()

    r = run(core)
    print(json.dumps({k: r[k] for k in ("trades", "active_days", "avg_net", "win", "t",
                                       "total", "ann", "dd", "sharpe", "calmar")},
                     ensure_ascii=False, indent=2))
    print("yearly", json.dumps(r["yearly"], ensure_ascii=False))
    print("quarterly", json.dumps(r["quarterly"], ensure_ascii=False))

    # 退出类型
    exit_types = {}
    for t, g in core.groupby("exit_type"):
        exit_types[t] = {"n": int(len(g)), "mean": round(float(g.net.mean()), 4),
                         "win": round(float((g.net > 0).mean()), 3),
                         "total": round(float((1 + g.net).prod() - 1), 4)}
    print("exit_types", json.dumps(exit_types, ensure_ascii=False))

    # 留一过滤
    loo = {}
    for name in FILTERS:
        m = pd.Series(True, index=d.index)
        for k, f in FILTERS.items():
            if k != name:
                m &= f(d)
        rr = run(d[m])
        if rr:
            loo[f"drop_{name}"] = {k: rr[k] for k in ("trades", "avg_net", "win", "ann", "dd")}
    print("loo", json.dumps(loo, ensure_ascii=False))

    # 单过滤变体
    variants = {"base": run(d), "core": r}
    for name, f in FILTERS.items():
        rr = run(d[f(d)])
        if rr is None:
            continue
        variants[f"only_{name}"] = {k: rr[k] for k in ("trades", "avg_net", "win", "ann", "dd")}
    print("variants", json.dumps({k: {kk: v[kk] for kk in ("trades", "avg_net", "win", "ann", "dd")}
                                  for k, v in variants.items()}, ensure_ascii=False))

    # bootstrap
    rng = np.random.default_rng(42)
    nets = core.net.values
    means, wins = [], []
    for _ in range(2000):
        b = rng.choice(nets, len(nets), replace=True)
        means.append(b.mean())
        wins.append((b > 0).mean())
    boot = {"mean_ci95": [round(float(np.quantile(means, 0.025)), 4),
                          round(float(np.quantile(means, 0.975)), 4)],
            "win_ci95": [round(float(np.quantile(wins, 0.025)), 4),
                         round(float(np.quantile(wins, 0.975)), 4)]}
    print("bootstrap", boot)

    # 成本敏感性
    cost_sens = {}
    for c in (0.0015, 0.0020, 0.0025, 0.0030, 0.0040):
        dd = core.copy()
        dd["net"] = dd.ret - c
        rr = run(dd)
        cost_sens[f"{c:.4f}"] = {k: rr[k] for k in ("avg_net", "win", "ann", "dd")}
    print("cost", json.dumps(cost_sens, ensure_ascii=False))

    # 仓位 20%
    picks = core.groupby("signal_date").head(5)
    slot = (picks.groupby("signal_date")["net"].apply(lambda x: x.sum() * 0.2)).reindex(ALL_DAYS).fillna(0)
    eqs = (1 + slot).cumprod()
    slot_stats = {"ann": round(float(eqs.iloc[-1] ** (365.25 / (slot.index.max() - slot.index.min()).days) - 1), 4),
                  "total": round(float(eqs.iloc[-1] - 1), 4),
                  "dd": round(float((eqs / eqs.cummax() - 1).min()), 4)}

    # 月度
    monthly = [{"ym": f"{y}-{m:02d}", "ret": round(float(v), 4)}
               for (y, m), v in (1 + r["active"]).groupby(
                   [r["active"].index.year, r["active"].index.month]).prod().items()]

    # 最差 / 最好
    worst = core.nsmallest(8, "net")[["signal_date", "ts_code", "exit_type", "ret", "net"]]
    best = core.nlargest(8, "net")[["signal_date", "ts_code", "exit_type", "ret", "net"]]

    out = {
        "stats": {
            "period": [str(core.signal_date.min().date()), str(core.signal_date.max().date())],
            "trades": r["trades"], "active_days": r["active_days"],
            "avg_trade_net": r["avg_net"], "median_trade_net": r["median_net"],
            "win_rate": r["win"], "t_stat": r["t"],
            "total_return": r["total"], "annualized": r["ann"],
            "max_drawdown": r["dd"], "sharpe": r["sharpe"], "calmar": r["calmar"],
            "max_win_streak": int(max((len(list(g)) for k, g in __import__("itertools").groupby(core.net > 0) if k), default=0)),
            "max_loss_streak": int(max((len(list(g)) for k, g in __import__("itertools").groupby(core.net > 0) if not k), default=0)),
            "avg_trades_per_active_day": round(len(core) / core.signal_date.nunique(), 2),
            "cost": COST, "slot20": slot_stats,
            "bootstrap": boot, "by_exit": exit_types,
            "yearly": r["yearly"], "quarterly": r["quarterly"],
            "cost_sensitivity": cost_sens, "loo": loo,
            "variants": {k: {kk: v[kk] for kk in ("trades", "avg_net", "win", "ann", "dd")}
                         for k, v in variants.items()},
        },
        "monthly": monthly,
        "equity": [{"date": str(k.date()), "equity": round(float(v), 4),
                    "drawdown": round(float(r["dd_series"].loc[k]), 4)} for k, v in r["eq_series"].items()],
        "trades": [{"date": str(x.signal_date.date()), "code": x.ts_code,
                    "pct1430": round(float(x.pct1430), 2), "pos1430": round(float(x.pos1430), 3),
                    "vwap_pos": round(float(x.vwap_pos), 4), "lu20": int(x.prev_lu20),
                    "ret5": round(float(x.prev_ret5), 4), "amt_ratio": round(float(x.amount_ratio), 2),
                    "exit": x.exit_type, "ret": round(float(x.ret), 4), "net": round(float(x.net), 4)}
                   for x in core.itertuples()],
        "worst": worst.assign(signal_date=worst.signal_date.dt.strftime("%Y-%m-%d")).to_dict("records"),
        "best": best.assign(signal_date=best.signal_date.dt.strftime("%Y-%m-%d")).to_dict("records"),
        "rule": {
            "scan": "14:30", "entry": "14:40-15:00", "exit": "次日10:00前",
            "filters": ["pos1430<0.2", "pct1430 -7%~-2%", "turnover1.5-8%", "b20<=0.7",
                        "vwap_pos>-2%", "prev_lu20<=1", "prev_ret5>-2%", "amount_ratio>=0.8"],
            "exit_rule": "09:25 浮盈>=+0.5% 或浮亏<=-3% 开盘卖；否则 -2% 止损，10:00 前卖",
        },
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "v3_backtest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    core.assign(signal_date=core.signal_date.dt.strftime("%Y-%m-%d")).to_csv(
        os.path.join(OUT, "v3_trades.csv"), index=False, encoding="utf-8-sig")
    print("saved", OUT)


if __name__ == "__main__":
    main()
