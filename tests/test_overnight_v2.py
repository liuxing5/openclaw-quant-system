# -*- coding: utf-8 -*-
"""隔夜八步法 v2（恐慌缺口承接）纯函数测试。

直接运行: python tests/test_overnight_v2.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategies.overnight_8step import v2_engine as v2

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def _feat(n=8):
    """构造可通过全部条件的行情，再逐项破坏。"""
    rows = []
    for i in range(n):
        rows.append({
            "ts_code": f"60000{i}.SH",
            "stock_name": f"测试{i}",
            "open": 10.0,
            "high": 10.6,
            "low": 9.6,
            "close": 9.8,          # 收盘位于振幅 20% -> 恐慌收盘
            "pct_chg": -3.0,
            "turnover_rate": 3.0,
            "amount": 2e8,
            "circulating_market_cap": 100e8,
            "ma20": 9.5,
            "ma60": 9.0,
            "ret20": 0.08,
            "is_st": False,
            "list_date": pd.Timestamp("2015-01-01"),
        })
    return pd.DataFrame(rows)


def test_regime():
    print("[market_regime]")
    dates = pd.bdate_range("2026-01-01", periods=15)
    up = pd.DataFrame({"trade_date": dates, "mean_pct": [0.5] * 15})
    down = pd.DataFrame({"trade_date": dates, "mean_pct": [-0.5] * 15})
    check("单边上涨 -> risk_on", v2.market_regime(up)["risk_on"] is True)
    check("单边下跌 -> risk_off", v2.market_regime(down)["risk_on"] is False)
    check("指数在均线上", v2.market_regime(up)["index"] > v2.market_regime(up)["ma"])


def test_gate():
    print("[gate]")
    on = {"risk_on": True}
    check("risk_on + 广度0.5 -> 不拦截", v2.gate(on, 0.5) is False)
    check("risk_on + 广度0.2 -> 拦截", v2.gate(on, 0.2) is True)
    check("risk_off + 广度0.9 -> 拦截", v2.gate({"risk_on": False}, 0.9) is True)


def test_apply_steps():
    print("[apply_steps]")
    cfg = dict(v2.CONFIG)
    cfg["_today"] = "2026-05-20"
    base = _feat()
    out = v2.apply_steps(base, 0.5, cfg)
    check("全部满足 -> 8 条", len(out) == 8, len(out))
    check("排序按收盘位置升序", list(out["close_pos"]) == sorted(out["close_pos"]))
    check("评分在 60~100", out["score"].between(60, 100).all())

    def blocked(col, val):
        f = _feat()
        f[col] = val
        return len(v2.apply_steps(f, 0.5, cfg))

    check("趋势破坏(close<ma20) -> 0", blocked("close", 9.0) == 0)
    check("回踩过深(-7%) -> 0", blocked("pct_chg", -7.0) == 0)
    check("回踩过浅(-0.5%) -> 0", blocked("pct_chg", -0.5) == 0)
    check("收盘偏强(位置0.9) -> 0", blocked("close", 10.5) == 0)
    check("换手过低(1.0%) -> 0", blocked("turnover_rate", 1.0) == 0)
    check("换手过高(12%) -> 0", blocked("turnover_rate", 12.0) == 0)
    check("成交额不足 -> 0", blocked("amount", 5e7) == 0)
    check("市值超上限 -> 0", blocked("circulating_market_cap", 5000e8) == 0)
    check("ST -> 0", blocked("is_st", True) == 0)
    check("次新 -> 0", blocked("list_date", pd.Timestamp("2026-05-01")) == 0)

    f = _feat()
    f["high"] = f["low"]          # 一字板，分母 0
    check("一字板不崩溃", len(v2.apply_steps(f, 0.5, cfg)) == 0)


def test_gap():
    print("[gap]")
    check("gap_ok -2%", v2.gap_ok(-0.02) is True)
    check("gap_ok -1% 边界", v2.gap_ok(-0.01) is True)
    check("gap_ok -4% 边界", v2.gap_ok(-0.04) is True)
    check("gap_ok -0.5% 太浅", v2.gap_ok(-0.005) is False)
    check("gap_ok -5% 断崖", v2.gap_ok(-0.05) is False)
    bands = v2.gap_bands(10.0)
    check("承接区间 [9.6, 9.9]", bands == {"entry_low": 9.6, "entry_high": 9.9}, bands)


def test_to_rows():
    print("[_to_rows]")
    feat = _feat(2)
    cfg = dict(v2.CONFIG)
    cfg["_today"] = "2026-05-20"
    cand = v2.apply_steps(feat, 0.5, cfg)
    on = {"trade_date": "2026-05-20", "blocked": False, "breadth": 0.5,
          "regime": {"risk_on": True}, "candidates": cand}
    rows = v2._to_rows(on, cfg)
    check("risk_on -> selected", all(r["selected"] for r in rows))
    check("仓位 20%", all(r["position_pct"] == 0.2 for r in rows))
    check("标签含 T+2 离场", any("T+2收盘离场" in r["logic_tags"] for r in rows))

    off = dict(on, blocked=True)
    rows = v2._to_rows(off, cfg)
    check("门控关闭 -> selected False", all(not r["selected"] for r in rows))
    check("门控关闭 -> 仓位 0", all(r["position_pct"] == 0 for r in rows))
    check("门控关闭 -> 观察标签", any("观察不买" in t for r in rows for t in r["logic_tags"]))


def test_step_stats():
    print("[step_stats]")
    stats = v2.step_stats(_feat(5))
    check("全市场=5", stats["全市场"] == 5, stats)
    check("漏斗单调不增",
          stats["全市场"] >= stats["趋势结构"] >= stats["回踩幅度"] >= stats["恐慌收盘"]
          >= stats["流动性"] >= stats["换手适中"], stats)


def main():
    test_regime()
    test_gate()
    test_apply_steps()
    test_gap()
    test_to_rows()
    test_step_stats()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
