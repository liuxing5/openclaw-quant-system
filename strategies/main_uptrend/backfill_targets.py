"""
定向回填目标股票历史K线数据
=============================
针对数据不足的股票，使用 baostock 拉取历史K线写入 daily_quotes。
"""
from __future__ import annotations

import os
import sys
import time
import socket
from datetime import datetime, timedelta, timezone

import baostock as bs
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db_fresh
from core.utils.ts_code import standard_to_baostock

socket.setdefaulttimeout(15)
BEIJING_TZ = timezone(timedelta(hours=8))

TARGET_STOCKS = [
    '603115.SH',
    '600578.SH',
    '688008.SH',
    '603688.SH',
    '600707.SH',
    '605358.SH',
]

FIELDS = "date,open,high,low,close,volume,amount,turn,pctChg"


def fetch_stock_history(bs_code: str, start_date: str, end_date: str) -> list:
    try:
        rs = bs.query_history_k_data_plus(
            bs_code, FIELDS,
            start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="3",
        )
        if rs.error_code != '0':
            return []
        rows = []
        while rs.next():
            row = rs.get_row_data()
            if not row or len(row) < 9 or not row[0]:
                continue
            try:
                trade_date = datetime.strptime(row[0], '%Y-%m-%d').date()
            except (ValueError, TypeError):
                continue

            def _f(val, default=None):
                if val is None or val == '':
                    return default
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default

            close = _f(row[4])
            if close is None or close <= 0:
                continue
            rows.append((
                trade_date,
                _f(row[1]),
                _f(row[2]),
                _f(row[3]),
                close,
                int(_f(row[5], 0)),
                _f(row[6], 0),
                _f(row[7], 0),
                _f(row[8], 0),
            ))
        return rows
    except socket.timeout:
        return []
    except Exception:
        return []


def insert_rows(ts_code: str, rows: list) -> int:
    if not rows:
        return 0
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        values = [(ts_code,) + r for r in rows]
        execute_values(cur, """
            INSERT INTO daily_quotes (
                ts_code, trade_date, open, high, low, close,
                volume, amount, turnover_rate, pct_chg
            ) VALUES %s
            ON CONFLICT (ts_code, trade_date) DO NOTHING
        """, values, page_size=500)
        inserted = cur.rowcount
        conn.commit()
        cur.close()
        return inserted
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"  {ts_code} write failed: {e}")
        return 0
    finally:
        if conn and not conn.closed:
            conn.close()


def main():
    end_dt = datetime.now(BEIJING_TZ).date()
    start_dt = end_dt - timedelta(days=700)

    print("=" * 60)
    print("  Target stocks backfill")
    print(f"  Date range: {start_dt} ~ {end_dt}")
    print(f"  Stocks: {len(TARGET_STOCKS)}")
    print("=" * 60)

    lg = bs.login()
    if lg.error_code != '0':
        print(f"baostock login failed: {lg.error_msg}")
        return
    print("baostock login OK")

    start_str = start_dt.strftime('%Y-%m-%d')
    end_str = end_dt.strftime('%Y-%m-%d')

    total_inserted = 0
    for ts_code in TARGET_STOCKS:
        bs_code = standard_to_baostock(ts_code)
        print(f"  {ts_code} ({bs_code})...", end=" ", flush=True)
        rows = fetch_stock_history(bs_code, start_str, end_str)
        if not rows:
            print("NO DATA")
            continue
        inserted = insert_rows(ts_code, rows)
        total_inserted += inserted
        print(f"{len(rows)} rows, {inserted} inserted")
        time.sleep(0.3)

    bs.logout()

    print(f"\nDone: {total_inserted} rows inserted for {len(TARGET_STOCKS)} stocks")

    # Verify
    print("\n--- Verification ---")
    conn = get_db_fresh()
    cur = conn.cursor()
    for ts_code in TARGET_STOCKS:
        cur.execute("""
            SELECT COUNT(*), MIN(trade_date), MAX(trade_date)
            FROM daily_quotes WHERE ts_code = %s
        """, (ts_code,))
        cnt, mn, mx = cur.fetchone()
        print(f"  {ts_code}: {cnt} rows, {mn} ~ {mx}")
    cur.close()
    conn.close()

    from core.db.connection import close_all_pools
    close_all_pools()


if __name__ == '__main__':
    main()
