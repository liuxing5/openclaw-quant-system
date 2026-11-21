"""14:30 扫描 / 14:50 买入 / 次日早盘卖出 —— 5 分钟线样本回测。

数据：data/research/1430_signals.csv（600 个信号，2025-01 ~ 2026-04，baostock 5m）
口径：14:30 特征选股，14:50 买入；次日 09:25 看浮盈/浮亏，
      浮盈≥+0.5% 或浮亏≤-3% 开盘卖，否则 -2% 止损、09:50 前卖。
"""
import json
import os

import numpy as np
import pandas as pd

COST = 0.0025
SRC = "data/research/1430_signals.csv"
OUT = "docs/overnight_v2"


def main():
    d = pd.read_csv(SRC)
    d["signal_date"] = pd.to_datetime(d.signal_date)
    d["exit_gap"] = np.where(d.gap_open >= 0.005, d.gap_open,
                             np.where(d.gap_open <= -0.03, d.gap_open, d.stop2))

    m = ((d.pos1430 < 0.2) & d.pct1430.between(-7, -2) & d.turn1430.between(1.5, 8)
         & (d.b20 <= 0.7) & d.exit_gap.notna())
    s = d[m].copy().sort_values(["signal_date", "pos1430"])
    picks = s.groupby("signal_date").head(5)

    all_days = pd.bdate_range("2025-01-01", "2026-04-21")
    daily = (picks.groupby("signal_date").exit_gap.mean() - COST).reindex(all_days).fillna(0.0)
    eq = (1 + daily).cumprod()
    dd = eq / eq.cummax() - 1
    years = (daily.index.max() - daily.index.min()).days / 365.25
    x = s.exit_gap
    tr = daily[daily.index < "2026-01-01"]
    te = daily[daily.index >= "2026-01-01"]

    stats = {
        "period": [str(d.signal_date.min().date()), str(d.signal_date.max().date())],
        "trades": int(len(s)),
        "active_days": int(s.signal_date.nunique()),
        "avg_trade_ret": round(float(x.mean() - COST), 4),
        "win_rate": round(float((x > COST).mean()), 4),
        "t_stat": round(float(x.mean() / (x.std() / np.sqrt(len(x)))), 2),
        "total_return": round(float(eq.iloc[-1] - 1), 4),
        "annualized": round(float(eq.iloc[-1] ** (1 / years) - 1), 4),
        "max_drawdown": round(float(dd.min()), 4),
        "sharpe": round(float(daily.mean() / daily.std() * np.sqrt(252)), 2),
        "year_2025": round(float((1 + tr).prod() - 1), 4),
        "year_2026": round(float((1 + te).prod() - 1), 4),
        "cost": COST,
        "sample": "600 个 14:30 候选信号的 baostock 5 分钟线样本（非全量）",
        "exit_rule": "09:25 浮盈≥+0.5% 或浮亏≤-3% 开盘卖；否则 -2% 止损，09:50 前卖",
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    monthly = (1 + daily).groupby([daily.index.year, daily.index.month]).prod() - 1
    out = {
        "stats": stats,
        "monthly": [{"ym": f"{y}-{m:02d}", "ret": round(float(v), 4)} for (y, m), v in monthly.items()],
        "equity": [{"date": str(k.date()), "equity": round(float(v), 4), "drawdown": round(float(dd.loc[k]), 4)}
                   for k, v in eq.items()],
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "intraday_backtest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    cols = ["signal_date", "ts_code", "pct1430", "pos1430", "turn1430", "b20",
            "gap_open", "r0940", "stop2", "exit_gap"]
    s[cols].assign(signal_date=s.signal_date.dt.strftime("%Y-%m-%d")).to_csv(
        os.path.join(OUT, "intraday_trades.csv"), index=False, encoding="utf-8-sig")
    print("\nsaved", OUT)


if __name__ == "__main__":
    main()
