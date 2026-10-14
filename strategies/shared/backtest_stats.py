"""回测统计共享模块 — 交易成本模型 + 显著性检验 + 非重叠窗口采样

背景（2026-10-13 参数审查）：
  1. 此前所有回测均不计交易成本，A股双向约 0.25% 的成本足以吃掉多数策略的微利；
  2. 前向收益统计用重叠窗口（每个交易日都算一笔），样本自相关导致 t 值虚高；
  3. 页面只报 win_rate/mean，没有显著性，无法区分"真信号"和"噪声"。

本模块统一三处口径：
  - 成本: 买入 0.10%（佣金+滑点）+ 卖出 0.15%（佣金+印花税 0.05%+滑点），往返 0.25%；
  - 显著性: 均值 t 检验，重叠窗口用 n_eff = count / horizon 校正有效样本量；
  - 非重叠采样: 按交易日历贪心抽取间隔 >= horizon 的信号，给出独立子样本统计。

调用方:
  - strategies/pullback_strategy/run_backtest.py
  - strategies/main_uptrend/backtester.py / run_backtest.py
  - strategies/funnel_strategy/forward_stats.py（漏斗 / LLM 多源 / 八步法前向收益重算）
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np

# 单边成本（费率，非百分数）
COST_BUY = 0.0010    # 佣金约 0.025% + 滑点约 0.075%
COST_SELL = 0.0015   # 佣金约 0.025% + 印花税 0.05% + 滑点约 0.075%
ROUND_TRIP_COST = COST_BUY + COST_SELL  # 0.25%


def net_return(gross_ret: float) -> float:
    """毛收益 -> 扣除往返成本后的净收益（乘法口径）。"""
    return (1.0 + gross_ret) / (1.0 + ROUND_TRIP_COST) - 1.0


def net_returns(gross) -> np.ndarray:
    """批量版 net_return，输入 list/ndarray，返回 float64 ndarray。"""
    arr = np.asarray(gross, dtype=np.float64)
    return (1.0 + arr) / (1.0 + ROUND_TRIP_COST) - 1.0


def _norm_sf(z: float) -> float:
    """标准正态分布上尾概率（scipy 不可用时的回退）。"""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


try:  # scipy 可用时用 Student-t 分布（小样本/低自由度下 p 值更诚实）
    from scipy import stats as _sp_stats

    def _t_sf(t: float, df: int) -> float:
        if df < 1:
            return 1.0
        return float(2.0 * _sp_stats.t.sf(abs(t), df))

    def _t_ppf(p: float, df: int) -> float:
        if df < 1:
            return 0.0
        return float(_sp_stats.t.ppf(1.0 - p / 2.0, df))
except Exception:  # pragma: no cover
    def _t_sf(t: float, df: int) -> float:
        return _norm_sf(t)

    def _t_ppf(p: float, df: int) -> float:
        return 1.96


LOW_POWER_MIN = 10  # 有效样本量低于该值时标注"低功效"，不作显著性结论


def t_stat(values: Sequence[float], n_eff: Optional[int] = None) -> Dict[str, float]:
    """均值 t 检验。

    values: 样本（已扣成本）
    n_eff : 有效样本量。重叠窗口下传入 count // horizon 做校正；
            传 None 表示按原始样本量（非重叠或独立样本）。
    返回 {t, p, mean, std, ci95_low, ci95_high, n, n_eff, low_power}
      low_power=True 表示 n_eff < 10，p/CI 仅供参照、不作显著性结论。
    """
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    n = int(arr.size)
    if n == 0:
        return {"t": 0.0, "p": 1.0, "mean": 0.0, "std": 0.0,
                "ci95_low": 0.0, "ci95_high": 0.0, "n": 0, "n_eff": 0,
                "low_power": True}
    mean = float(arr.mean())
    if n < 2:
        return {"t": 0.0, "p": 1.0, "mean": mean, "std": 0.0,
                "ci95_low": mean, "ci95_high": mean, "n": n, "n_eff": n,
                "low_power": True}
    std = float(arr.std(ddof=1))
    n_eff = int(n if n_eff is None else max(1, min(n_eff, n)))
    if std == 0:
        return {"t": 0.0, "p": 1.0, "mean": mean, "std": 0.0,
                "ci95_low": mean, "ci95_high": mean, "n": n, "n_eff": n_eff,
                "low_power": n_eff < LOW_POWER_MIN}
    se = std / math.sqrt(n_eff)
    t = mean / se
    df = max(1, n_eff - 1)
    p = _t_sf(t, df)
    ci = _t_ppf(0.05, df) * se
    return {"t": float(t), "p": float(p), "mean": mean, "std": std,
            "ci95_low": mean - ci, "ci95_high": mean + ci,
            "n": n, "n_eff": n_eff,
            "low_power": n_eff < LOW_POWER_MIN}


def cluster_t_stat(values: Sequence[float], dates: Sequence,
                   horizon: int = 1) -> Dict[str, float]:
    """按评估日聚类的 t 检验（修截面相关 + 时间重叠双重校正）。

    逐样本 t 检验假设样本独立，但 (a) 同日多只股票共享市场因子（截面相关），
    (b) 前向窗口跨日重叠。本函数先取每日截面均值形成日收益序列，
    再以 n_eff = 日数 // horizon 做重叠校正后检验。
    horizon=1 时即"按日聚类"（同日相关性由聚合本身消除）。
    """
    if len(values) == 0 or len(dates) != len(values):
        return {**t_stat(values), "n_days": 0, "method": "naive"}
    by_day: Dict = {}
    for v, d in zip(values, dates):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        by_day.setdefault(d, []).append(float(v))
    if not by_day:
        return {**t_stat(values), "n_days": 0, "method": "naive"}
    daily = [float(np.mean(vs)) for _, vs in sorted(by_day.items(),
                                                    key=lambda kv: str(kv[0]))]
    n_days = len(daily)
    n_eff = max(1, n_days // max(1, horizon))
    st = t_stat(daily, n_eff=n_eff)
    st["n_days"] = n_days
    st["method"] = "cluster_by_date"
    return st


def nonoverlap_indices(dates: Sequence, horizon: int,
                       trading_days: Optional[Sequence] = None) -> List[int]:
    """贪心抽取前向窗口不重叠的样本下标。

    dates: 每个样本的评估日（datetime.date / np.datetime64 / str 均可）
    horizon: 前向窗口（交易日）
    trading_days: 升序交易日列表；传入时按交易日序号间隔 >= horizon 判定，
                  否则退化为自然日间隔 >= horizon * 1.5 天的近似。
    返回 dates 中被选中样本的下标列表（保持时间升序）。
    """
    if horizon <= 0 or len(dates) == 0:
        return list(range(len(dates)))

    def _to_date(v):
        import datetime as _dt
        if isinstance(v, _dt.datetime):
            return v.date()
        if isinstance(v, _dt.date):
            return v
        s = str(v)[:10]
        return _dt.date.fromisoformat(s)

    days_idx = {}
    if trading_days:
        days_idx = {_to_date(d): i for i, d in enumerate(trading_days)}

    order = sorted(range(len(dates)), key=lambda i: _to_date(dates[i]))
    picked: List[int] = []
    last_pos: Optional[int] = None
    last_date = None
    for i in order:
        d = _to_date(dates[i])
        if not picked:
            picked.append(i)
            last_date = d
            last_pos = days_idx.get(d, 0) if days_idx else None
            continue
        if days_idx:
            # 非交易日样本退化用上一个选中样本的位置
            pos = days_idx.get(d, last_pos if last_pos is not None else 0)
            if pos - (last_pos or 0) >= horizon:
                picked.append(i)
                last_pos = pos
                last_date = d
        else:
            # 无交易日历：自然日间隔近似（horizon 个交易日 ≈ 1.5 倍自然日）
            if (d - last_date).days >= int(math.ceil(horizon * 1.5)):
                picked.append(i)
                last_date = d
    return sorted(picked)


def fwd_stats(values: Sequence[float], dates: Optional[Sequence] = None,
              horizon: int = 0, trading_days: Optional[Sequence] = None) -> Dict:
    """前向收益统一统计（values 应已用 net_returns 扣成本）。

    返回:
      count, win_rate, mean, median, std          —— 全样本（重叠窗口）
      t, p, ci95_low, ci95_high, n_eff            —— 显著性：有 dates 时
                                                     按日聚类（cluster_t_stat，
                                                     修截面相关+重叠），否则
                                                     n_eff=count//horizon 校正
      low_power                                   —— True 时结论不显著/仅参照
      nonoverlap: {count, win_rate, mean, t, p, low_power}
    """
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    n = int(arr.size)
    if n == 0:
        return {"count": 0, "win_rate": 0.0, "mean": 0.0, "median": 0.0, "std": 0.0,
                "t": 0.0, "p": 1.0, "ci95_low": 0.0, "ci95_high": 0.0, "n_eff": 0,
                "low_power": True,
                "nonoverlap": {"count": 0, "win_rate": 0.0, "mean": 0.0,
                               "t": 0.0, "p": 1.0, "low_power": True}}
    mean = float(arr.mean())
    if dates is not None and len(dates) == n and horizon > 0:
        # 按评估日聚类：消除同日截面相关，n_eff = 日数 // horizon 消除窗口重叠
        sig = cluster_t_stat(arr, dates, horizon=horizon)
        sig_source = "cluster_by_date"
    else:
        sig = t_stat(arr, n_eff=(n // horizon if horizon else None))
        sig_source = "n_eff"
    result = {
        "count": n,
        "win_rate": float((arr > 0).mean()),
        "mean": mean,
        "median": float(np.median(arr)),
        "std": float(arr.std(ddof=1)) if n > 1 else 0.0,
        "t": sig["t"], "p": sig["p"],
        "ci95_low": sig["ci95_low"], "ci95_high": sig["ci95_high"],
        "n_eff": sig["n_eff"],
        "low_power": bool(sig.get("low_power", sig["n_eff"] < LOW_POWER_MIN)),
        "sig_source": sig_source,
        "n_days": int(sig.get("n_days", 0)),
    }
    if dates is not None and horizon > 0 and len(dates) == n:
        idx = nonoverlap_indices(dates, horizon, trading_days)
        sub = arr[idx]
        if sub.size:
            st = t_stat(sub)
            result["nonoverlap"] = {
                "count": int(sub.size),
                "win_rate": float((sub > 0).mean()),
                "mean": float(sub.mean()),
                "t": st["t"], "p": st["p"],
                "low_power": bool(st.get("low_power", False)),
            }
        else:
            result["nonoverlap"] = {"count": 0, "win_rate": 0.0, "mean": 0.0,
                                    "t": 0.0, "p": 1.0, "low_power": True}
    else:
        result["nonoverlap"] = {"count": 0, "win_rate": 0.0, "mean": 0.0,
                                "t": 0.0, "p": 1.0, "low_power": True}
    return result


def format_sig(stats: Dict) -> str:
    """把 fwd_stats / t_stat 摘要格式化为页面用的显著性短语。

    't=2.41**' / 't=-1.2 (低功效)' —— low_power 时不加星号并明示。
    """
    if not stats:
        return "—"
    t = stats.get("t", 0.0)
    p = stats.get("p", 1.0)
    if stats.get("low_power"):
        return f"t={t:.2f}（低功效，不作结论）"
    if p < 0.01:
        mark = "**"
    elif p < 0.05:
        mark = "*"
    else:
        mark = ""
    return f"t={t:.2f}{mark}"


COST_NOTE = "含交易成本 0.25%（买 0.10%+卖 0.15%）"
SIGNIFICANCE_NOTE = (
    "* p<0.05，** p<0.01；t 检验用 Student-t 分布，"
    "按评估日聚类（同日截面相关聚合为日均值）并以 日数//窗口 校正重叠；"
    "n_eff<10 标注'低功效'不作显著性结论；多重比较未校正，"
    "参数与评估同区间，p 值偏乐观"
)
