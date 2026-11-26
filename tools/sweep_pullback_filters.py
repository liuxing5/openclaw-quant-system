"""回调买入改造验证：用 10459 笔历史成交明细离线扫描过滤条件。

诊断缺点：
  1) final_score 与 5 日收益 IC=-0.366（分越高越差）→ 验证按分数分桶的收益；
  2) 回测无资金约束（397 并发）→ 验证并发上限下的收益/回撤；
  3) 2026 年子期退化 → 验证宽度门控与止损口径的影响。
数据：strategies/funnel_strategy/docs/pullback_backtest.csv（2025-01~2026-10）
输出：docs/pullback_filter_sweep.json
"""
import json

import numpy as np
import pandas as pd

SRC = "strategies/funnel_strategy/docs/pullback_backtest.csv"
OUT = "docs/pullback_filter_sweep.json"


def cluster_t(d, col="exit_ret", dates="eval_date"):
    g = d.groupby(dates)[col].mean()
    if len(g) < 2:
        return None
    return float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g))))


def stat(d, label):
    if d.empty:
        return {"label": label, "n": 0}
    r = d["exit_ret"]
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    return {
        "label": label, "n": int(len(d)),
        "win": round(float((r > 0).mean()), 4),
        "mean": round(float(r.mean()), 4),
        "t_cluster": round(cluster_t(d), 2) if cluster_t(d) is not None else None,
        "total": round(float(eq.iloc[-1] - 1), 4),
        "dd": round(float(dd.min()), 4),
    }


def main():
    d = pd.read_csv(SRC, encoding="utf-8-sig")
    d["eval_date"] = pd.to_datetime(d["eval_date"])
    out = {}
    out["baseline"] = stat(d, "基线 10459 笔")
    # 1) 分数分桶：验证 IC 反向
    d["score_bin"] = pd.qcut(d["composite_score"], 5, labels=False, duplicates="drop")
    for b, g in d.groupby("score_bin"):
        out[f"score_q{b+1}"] = stat(g, f"评分五分位 Q{b+1}")
    # 2) 只取低分组（IC 反向的修正方向）
    for q in (1, 2, 3):
        out[f"score_low{q}"] = stat(d[d["score_bin"] <= q - 1], f"取最低 {q} 档评分")
    # 3) 按出场类型：止损/止盈/超时/时间止损的贡献
    for tp, g in d.groupby("exit_type"):
        out[f"exit_{tp}"] = stat(g, f"仅 {tp} 出场")
    # 4) 持有期分层
    d["hold_bin"] = pd.cut(d["hold_days"], [0, 3, 5, 8, 10], labels=["1-3", "4-5", "6-8", "9-10"])
    for b, g in d.groupby("hold_bin", observed=True):
        out[f"hold_{b}"] = stat(g, f"持有 {b} 日")
    # 5) 并发约束模拟：每日最多开 N 仓（按 eval_date 截断，不给未来资金）
    for cap in (3, 5, 8, 12):
        sub = d.sort_values(["eval_date", "composite_score"]).copy()
        sub["rank_in_day"] = sub.groupby("eval_date").cumcount()
        out[f"cap{cap}"] = stat(sub[sub["rank_in_day"] < cap], f"每日最多 {cap} 只")
    # 6) 分年度
    d["year"] = d["eval_date"].dt.year
    for y, g in d.groupby("year"):
        out[f"year_{y}"] = stat(g, f"{y} 年")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    for k, v in out.items():
        print(f"{k:18s} n={v.get('n',0):5d} win={v.get('win',0):.1%} "
              f"mean={v.get('mean',0):+.2%} t={v.get('t_cluster')} "
              f"total={v.get('total',0):+.1%} dd={v.get('dd',0):+.1%}")


if __name__ == "__main__":
    main()
