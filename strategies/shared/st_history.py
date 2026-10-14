"""ST 状态按日查询（Point-in-Time）— 修复"用当前名称回溯历史"的未来函数

背景（2026-10-14 审查）：stock_basic_info.is_st 是当前快照，直接套用到
历史回测期构成 look-ahead（历史上曾 ST 后摘帽的股票被未来信息放行，反之亦然）。

数据源优先级：
  1. stock_st_history 区间表（ts_code, st_name, start_date, end_date）——
     由 tushare namechange 或 baostock isST 回填（backfill_st_history.py）；
  2. 回退 stock_basic_info 当前快照（旧行为），并记 warning 一次。

用法：
    from strategies.shared.st_history import is_st_mask
    mask = is_st_mask(["000001.SZ", "600000.SH"], date(2025, 6, 30))  # np.ndarray[bool]
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, List, Sequence

import numpy as np

logger = logging.getLogger(__name__)

_table_state: dict = {}
_warned = False


def _has_history_table(conn) -> bool:
    if "has_table" not in _table_state:
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'stock_st_history' LIMIT 1""")
            _table_state["has_table"] = cur.fetchone() is not None
            cur.close()
        except Exception:
            _table_state["has_table"] = False
    return bool(_table_state["has_table"])


def is_st_mask(conn, codes: Sequence[str], on_date: date) -> np.ndarray:
    """返回与 codes 等长的 bool 数组：该代码在 on_date 当日是否 ST。"""
    global _warned
    codes = list(codes)
    if not codes:
        return np.zeros(0, dtype=bool)
    try:
        if _has_history_table(conn):
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT ts_code FROM stock_st_history
                WHERE start_date <= %s
                  AND (end_date IS NULL OR end_date >= %s)
                  AND ts_code = ANY(%s)""",
                (on_date, on_date, list(codes)))
            st = {r[0] for r in cur.fetchall()}
            cur.close()
            return np.array([c in st for c in codes], dtype=bool)
        # 回退：当前快照（旧行为，存在 look-ahead）
        if not _warned:
            logger.warning(
                "stock_st_history 表不存在，ST 过滤回退当前快照（存在未来函数，"
                "运行 backfill_st_history.py 可修复）")
            _warned = True
        cur = conn.cursor()
        cur.execute("""
            SELECT ts_code, is_st FROM stock_basic_info
            WHERE ts_code = ANY(%s)""", (list(codes),))
        m = {r[0]: bool(r[1]) for r in cur.fetchall()}
        cur.close()
        return np.array([m.get(c, False) for c in codes], dtype=bool)
    except Exception as exc:  # 查询失败不阻断主流程
        if not _warned:
            logger.warning("ST 历史查询失败，回退当前快照: %s", exc)
            _warned = True
        return np.zeros(len(codes), dtype=bool)
