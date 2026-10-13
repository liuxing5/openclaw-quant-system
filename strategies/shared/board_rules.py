"""板块涨停规则 — 修复"涨停口径"问题（2026-10-13 参数审查）

旧实现的问题：
  回调买入法用 `pct_chg < 9.9` 判"未涨停"，对创业板/科创板（±20%）、
  北交所（±30%）全部误判——20cm 票涨停日会被当作普通阳线买入。

新实现按板块计算真实涨停价：
  - 主板（60/00 前缀，.SH/.SZ）: ±10%
  - 创业板（300/301/302）: ±20%
  - 科创板（688/689）: ±20%
  - 北交所（.BJ）: ±30%
  - ST 股 ±5% 无法从代码识别，本系统选股已剔除 ST，不处理（已知限制）

涨停价四舍五入到分（A股交易所规则）。
"""
from __future__ import annotations

import math

_BOARD_PCT = 0.10


def limit_pct(ts_code: str) -> float:
    """返回该代码的单日涨跌幅限制（0.10 / 0.20 / 0.30）。"""
    code = ts_code.split(".")[0]
    if ts_code.endswith(".BJ"):
        return 0.30
    if code[:3] in ("300", "301", "302", "303"):
        return 0.20
    if code[:3] in ("688", "689"):
        return 0.20
    return _BOARD_PCT


def limit_up_price(ts_code: str, prev_close: float) -> float:
    """按板块规则计算涨停价（四舍五入到分）。"""
    pct = limit_pct(ts_code)
    return round(prev_close * (1.0 + pct) + 1e-8, 2)


def limit_down_price(ts_code: str, prev_close: float) -> float:
    pct = limit_pct(ts_code)
    return round(prev_close * (1.0 - pct) - 1e-8, 2)


def is_limit_up(ts_code: str, close: float, prev_close: float) -> bool:
    """close 是否触及/封住涨停价（容差 1 分）。"""
    if prev_close is None or prev_close <= 0 or close is None:
        return False
    return close >= limit_up_price(ts_code, prev_close) - 0.001


def is_limit_down(ts_code: str, close: float, prev_close: float) -> bool:
    if prev_close is None or prev_close <= 0 or close is None:
        return False
    return close <= limit_down_price(ts_code, prev_close) + 0.001
