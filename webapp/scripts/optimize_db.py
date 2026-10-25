# -*- coding: utf-8 -*-
"""为 Web 看板的高频聚合查询补齐索引。

全市场聚合（涨跌广度、情绪六维、连板梯队）反复按 trade_date 扫描 200 万行的
daily_quotes，缺少索引时每次都是顺序扫描，单次耗时 6~20 秒。本脚本幂等，
可重复执行。

用法：python webapp/scripts/optimize_db.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.utils.env import load_project_env  # noqa: E402

load_project_env()

import psycopg2  # noqa: E402

INDEXES = [
    ("idx_dq_trade_date", "daily_quotes", "(trade_date)"),
    ("idx_dq_ts_date", "daily_quotes", "(ts_code, trade_date DESC)"),
    ("idx_dq_trade_desc", "daily_quotes", "(trade_date DESC)"),
    ("idx_ix_trade_date", "index_quotes", "(trade_date)"),
    ("idx_sb_date", "stock_basic_info", "(stock_name)"),
    ("idx_lhb_date", "lhb_detail", "(trade_date)"),
    ("idx_cand_date", "daily_candidates", "(snapshot_date DESC)"),
    ("idx_sig_time", "raw_signals", "(pub_time DESC)"),
    ("idx_cq_date", "concept_board_quotes", "(trade_date)"),
    ("idx_iq_date", "industry_board_quotes", "(trade_date)"),
]


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    cur = conn.cursor()
    for name, table, cols in INDEXES:
        cur.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = %s",
            (name,),
        )
        if cur.fetchone():
            print(f"  skip  {name} (exists)")
            continue
        cur.execute(f"SELECT to_regclass(%s) IS NOT NULL", (table,))
        if not cur.fetchone()[0]:
            print(f"  skip  {name} (table {table} missing)")
            continue
        t = time.time()
        try:
            cur.execute(f"CREATE INDEX {name} ON {table} {cols}")
            print(f"  built {name} on {table} {cols} in {time.time() - t:.1f}s")
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name}: {str(e)[:160]}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
