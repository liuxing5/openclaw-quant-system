# -*- coding: utf-8 -*-
"""市场状态门控验证（直接运行 python tests/test_strategy_gates.py）

覆盖 2026-10-14 收益改造引入的三类门控：
  1. pullback: apply_breadth_gate 行为（阈值过滤 / 关闭开关 / 空信号）
  2. 各策略门控阈值配置留痕（reward_risk / min_market_breadth / max_breadth_ma20
     / 8step MIN_MARKET_BREADTH）——防静默回改
  3. main_uptrend data_loader.get_market_breadth 数据库口径（0..1 合法值）
"""
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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


def _mk_quotes():
    """构造 4 只股票 × 25 日行情：3 只线性上行(站上MA20)，1 只线性下行(跌破)；
    末日 2 只上行股单日暴跌至 MA20 下方 → 末日宽度 = 1/4 = 0.25。"""
    dates = pd.bdate_range("2025-06-02", periods=25)
    rows = []
    for i, d in enumerate(dates):
        closes = {
            "000001.SZ": 10.0 + 0.1 * i,           # 上行，末日暴跌
            "000002.SZ": 20.0 + 0.2 * i,           # 上行，末日暴跌
            "000003.SZ": 30.0 + 0.3 * i,           # 上行，末日不跌
            "000004.SZ": 40.0 - 0.4 * i,           # 一路下行
        }
        if i == len(dates) - 1:                    # 末日：001/002 暴跌 25%
            closes["000001.SZ"] *= 0.75
            closes["000002.SZ"] *= 0.75
        for code, c in closes.items():
            rows.append({"ts_code": code, "trade_date": d, "close": c})
    return pd.DataFrame(rows), dates


def test_pullback_breadth_gate():
    print("[pullback apply_breadth_gate]")
    from strategies.pullback_strategy.config import PullbackConfig
    from strategies.pullback_strategy.engine import apply_breadth_gate

    quotes, dates = _mk_quotes()
    hot_day, cold_day = dates[-2], dates[-1]     # 倒数第2日 / 末日

    def mk_sig(dates_):
        return pd.DataFrame({
            "ts_code": ["000001.SZ"] * len(dates_),
            "trade_date": list(dates_),
            "quant_score": [80.0] * len(dates_),
        })

    # 阈值 0.4：热日(0.75)保留，冷日(0.25)剔除
    cfg = PullbackConfig(min_market_breadth=0.4)
    sig = mk_sig([hot_day, cold_day])
    out = apply_breadth_gate(sig, quotes, cfg)
    check("0.4阈值保留热日信号", len(out) == 1 and out["trade_date"].iloc[0] == hot_day,
          f"got {len(out)} rows")
    check("剔除行不残留 _b20 列", "_b20" not in out.columns)

    # 关闭门控（0）：全部保留
    cfg_off = PullbackConfig(min_market_breadth=0.0)
    out_off = apply_breadth_gate(mk_sig([hot_day, cold_day]), quotes, cfg_off)
    check("门控=0 关闭时全保留", len(out_off) == 2, f"got {len(out_off)}")

    # 高阈值 0.8：热日 0.75 也不达标 → 全部剔除
    cfg_hi = PullbackConfig(min_market_breadth=0.8)
    out_hi = apply_breadth_gate(mk_sig([hot_day, cold_day]), quotes, cfg_hi)
    check("0.8阈值全部剔除", len(out_hi) == 0, f"got {len(out_hi)}")

    # 空信号：原样返回不报错
    empty = pd.DataFrame({"ts_code": [], "trade_date": []})
    out_empty = apply_breadth_gate(empty, quotes, cfg)
    check("空信号安全返回", len(out_empty) == 0)

    # 宽度口径抽验：末日宽度确为 0.25（ma20 已预热）
    ma20 = quotes.groupby("ts_code", sort=False)["close"].transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    valid = ma20.notna()
    num = ((quotes["close"] > ma20) & valid).groupby(quotes["trade_date"]).sum()
    den = valid.groupby(quotes["trade_date"]).sum().replace(0, np.nan)
    b = num / den
    check("末日宽度=0.25", abs(float(b.iloc[-1]) - 0.25) < 1e-9, f"got {b.iloc[-1]}")
    check("倒数第2日宽度=0.75", abs(float(b.iloc[-2]) - 0.75) < 1e-9, f"got {b.iloc[-2]}")
    # 预热期(ma20 全 NaN)宽度应为 NaN 而非 0
    check("预热期宽度=NaN", pd.isna(b.iloc[0]), f"got {b.iloc[0]}")


def test_threshold_config_invariants():
    print("[门控阈值配置留痕]")
    from strategies.pullback_strategy.config import PullbackConfig
    from strategies.main_uptrend.config import MainUptrendConfig

    pb = PullbackConfig()
    check("pullback reward_risk==3.0", pb.reward_risk == 3.0, f"got {pb.reward_risk}")
    check("pullback min_market_breadth==0.4", pb.min_market_breadth == 0.4,
          f"got {pb.min_market_breadth}")

    mu = MainUptrendConfig()
    check("main_uptrend max_breadth_ma20==0.6", mu.max_breadth_ma20 == 0.6,
          f"got {mu.max_breadth_ma20}")

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    bt8 = os.path.join(root, "strategies", "overnight_8step", "backtest_8step.py")
    src = open(bt8, encoding="utf-8").read()
    m = re.search(r"^MIN_MARKET_BREADTH = ([0-9.]+)", src, re.M)
    check("8step 回测门控阈值==0.5", m is not None and float(m.group(1)) == 0.5,
          f"match={m.group(1) if m else None}")
    check("8step 回测加载 .env", "_load_dotenv(" in src)

    zy1 = os.path.join(root, "strategies", "overnight_8step", "zuiyou1.py")
    src1 = open(zy1, encoding="utf-8").read()
    m1 = re.search(r"^MIN_MARKET_BREADTH = ([0-9.]+)", src1, re.M)
    check("8step 扫描器门控阈值==0.5", m1 is not None and float(m1.group(1)) == 0.5,
          f"match={m1.group(1) if m1 else None}")
    check("8step 扫描器接入宽度门控", "_market_breadth(" in src1 and
          "MIN_MARKET_BREADTH > 0" in src1)


def test_main_uptrend_breadth_source():
    print("[main_uptrend get_market_breadth DB 口径]")
    try:
        from strategies.main_uptrend.data_loader import DataLoader
        dl = DataLoader()
        # 用一个较早交易日（数据确定存在）验证 SQL 口径
        b = dl.get_market_breadth("2026-06-15")
    except Exception as e:  # noqa: BLE001
        check("get_market_breadth 可执行", False, repr(e))
        return
    b20 = b.get("breadth_ma20")
    check("breadth_ma20 为 [0,1] 数值",
          isinstance(b20, (int, float)) and 0.0 <= float(b20) <= 1.0,
          f"got {b20!r}")
    b5 = b.get("breadth_ma5")
    check("breadth_ma5 为 [0,1] 数值",
          isinstance(b5, (int, float)) and 0.0 <= float(b5) <= 1.0,
          f"got {b5!r}")


def test_uptrend_preload_offbyone():
    print("[main_uptrend preload 上界 off-by-one 修复]")
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    dl = os.path.join(root, "strategies", "main_uptrend", "data_loader.py")
    src = open(dl, encoding="utf-8").read()
    # 修前恒用 `trade_date < %s`（开区间），使 preload(start,end) 永不含 end 当日；
    # run_daily 调 preload(preload_start, eval_date) 时 eval_date 被排除 →
    # get_indicators_snapshot(eval_date) 恒空 → B/E 层自 2026-06-01 起恒 0
    # （分批重构 1d102ec4 引入）。修后：末批闭区间 `<=`、非末批开区间 `<`。
    # 断言防止静默回改。（函数级验证见 verify_uptrend_e2e / 手工 diag，
    #  preload 全市场需数分钟，不纳入快速单测。）
    check("preload 使用可变上界算子 op（末批<=，非末批<）",
          'op = "<=" if batch_end_dt >= ed else "<"' in src)
    check("preload SQL 上界用 {op} 而非硬编码 <",
          "trade_date {op} %s" in src and "trade_date < %s" not in src)


def main():
    test_pullback_breadth_gate()
    test_threshold_config_invariants()
    test_main_uptrend_breadth_source()
    test_uptrend_preload_offbyone()
    print("=" * 40)
    print(f"PASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
