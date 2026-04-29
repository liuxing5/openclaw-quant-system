"""
市场状态检测器
================================================
回答一个问题: 当前市场是否适合"主升前夜"策略?

判定逻辑(综合 4 个维度):
  D1 沪深 300 距 750 日(~3年)高点的回撤
     - >35%: 熊市末期 / 牛市初期 (★ 适合开策略)
     - 15-35%: 震荡市 (适合)
     - <15%: 牛市中段(高位)(不适合,主动空仓)

  D2 全市场触发率(L1 通过率)
     - >5%: 充足底部股 (适合)
     - 1-5%: 少量机会 (谨慎)
     - <1%: 极少机会 (空仓)

  D3 沪深 300 250 日斜率
     - <-10%: 下跌期(找不到底部)
     - ±10%: 震荡(适合)
     - >25%: 单边上涨期(策略易高位被套)

  D4 中位股回撤
     - 看 6494 只票的回撤分布,中位数超过 30% 才算真"熊市底部"

输出: 综合得分 0-100 + 仓位建议(0% / 30% / 60% / 100%)
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta

import pandas as pd

from data_loader import DataLoader, ensure_logged_in

logger = logging.getLogger(__name__)


def check_market_regime(loader: DataLoader = None,
                         sample_for_d2: int = 500,
                         verbose: bool = True) -> dict:
    """
    返回 dict:
        score: 0-100 综合得分
        regime: 文本描述
        position_suggestion: 0% / 30% / 60% / 100%
        details: 各维度细节
    """
    loader = loader or DataLoader()
    ensure_logged_in()

    # === D1: 沪深 300 距 750 日高点回撤 ===
    idx = loader.get_index("sh.000300", days=750)
    d1_score = 0
    d1_text = ""
    if idx is not None and len(idx) >= 250:
        last = float(idx["close"].iloc[-1])
        h750 = float(idx["high"].max())
        d1_dd = (h750 - last) / h750
        if d1_dd > 0.35:
            d1_score = 30
            d1_text = f"熊市末期/牛市初期 (回撤 {d1_dd:.1%})"
        elif d1_dd > 0.15:
            d1_score = 25
            d1_text = f"震荡市 (回撤 {d1_dd:.1%})"
        else:
            d1_score = 5
            d1_text = f"牛市高位 (回撤仅 {d1_dd:.1%})"
    else:
        d1_text = "指数数据不足"

    # === D2: 沪深 300 250 日斜率(用线性回归 slope) ===
    d2_score = 0
    d2_text = ""
    if idx is not None and len(idx) >= 250:
        recent = idx.tail(250).copy()
        x = pd.Series(range(len(recent)))
        y = recent["close"].astype(float).reset_index(drop=True)
        # 简单斜率: 250 日累计涨幅
        slope_pct = (y.iloc[-1] - y.iloc[0]) / y.iloc[0]
        if slope_pct < -0.10:
            d2_score = 5
            d2_text = f"下跌趋势 (250日 {slope_pct:+.1%})"
        elif -0.10 <= slope_pct <= 0.10:
            d2_score = 25
            d2_text = f"震荡 (250日 {slope_pct:+.1%})"
        elif 0.10 < slope_pct <= 0.25:
            d2_score = 20
            d2_text = f"温和上涨 (250日 {slope_pct:+.1%})"
        else:
            d2_score = 8
            d2_text = f"单边大涨 (250日 {slope_pct:+.1%}, 易追高被套)"
    else:
        d2_text = "数据不足"

    # === D3: 抽样股票的回撤分布 ===
    d3_score = 0
    d3_text = ""
    all_stocks = loader.get_all_stocks()
    if all_stocks is not None and not all_stocks.empty:
        sample = all_stocks.sample(min(sample_for_d2, len(all_stocks)),
                                    random_state=42)
        drawdowns = []
        for _, row in sample.iterrows():
            df = loader.get_kline(str(row["symbol"]), days=260)
            if df is None or len(df) < 250:
                continue
            last = float(df["close"].iloc[-1])
            h250 = float(df["high"].tail(250).max())
            if h250 > 0:
                drawdowns.append((h250 - last) / h250)
        if drawdowns:
            s = pd.Series(drawdowns)
            median_dd = s.median()
            pct_above_25 = (s >= 0.25).mean()
            pct_above_35 = (s >= 0.35).mean()
            d3_text = (f"中位回撤 {median_dd:.1%}, "
                       f"≥25% 占 {pct_above_25:.1%}, "
                       f"≥35% 占 {pct_above_35:.1%}")
            if median_dd >= 0.30:
                d3_score = 25
            elif median_dd >= 0.20:
                d3_score = 18
            elif median_dd >= 0.10:
                d3_score = 10
            else:
                d3_score = 3
        else:
            d3_text = "样本数据全部不足"
    else:
        d3_text = "全市场列表不可用"

    # === D4: 触发率估计(用 L1 通过率作代理) ===
    d4_score = 0
    d4_text = ""
    if all_stocks is not None and len(drawdowns) > 0:
        # 用 D3 的样本,估算 L1 通过率
        l1_passes = sum(1 for d in drawdowns if d >= 0.25)
        l1_pass_rate = l1_passes / len(drawdowns)
        d4_text = f"L1 通过率 {l1_pass_rate:.1%}"
        if l1_pass_rate >= 0.10:
            d4_score = 20
        elif l1_pass_rate >= 0.05:
            d4_score = 15
        elif l1_pass_rate >= 0.02:
            d4_score = 8
        else:
            d4_score = 2

    # === 综合 ===
    total = d1_score + d2_score + d3_score + d4_score
    if total >= 75:
        regime = "★ 强烈适合"
        position = "100% (满仓)"
    elif total >= 55:
        regime = "适合"
        position = "60% (中等仓位)"
    elif total >= 35:
        regime = "谨慎"
        position = "30% (轻仓试探)"
    else:
        regime = "✗ 不适合 (建议空仓)"
        position = "0% (空仓等待)"

    result = {
        "score": total,
        "regime": regime,
        "position_suggestion": position,
        "details": {
            "D1_index_drawdown": (d1_score, d1_text),
            "D2_index_slope": (d2_score, d2_text),
            "D3_market_drawdown_dist": (d3_score, d3_text),
            "D4_l1_pass_rate": (d4_score, d4_text),
        }
    }

    if verbose:
        print("\n" + "=" * 70)
        print("市场状态评估 — 当前是否适合'主升前夜'策略?")
        print("=" * 70)
        print(f"D1 沪深 300 距高点回撤:   {d1_score:>3}/30  {d1_text}")
        print(f"D2 沪深 300 250 日趋势:   {d2_score:>3}/25  {d2_text}")
        print(f"D3 全市场回撤分布(抽样): {d3_score:>3}/25  {d3_text}")
        print(f"D4 L1 通过率(抽样估计):  {d4_score:>3}/20  {d4_text}")
        print("-" * 70)
        print(f"综合得分: {total}/100")
        print(f"市场状态: {regime}")
        print(f"建议仓位: {position}")
        print("=" * 70)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    check_market_regime(sample_for_d2=300)
