# -*- coding: utf-8 -*-
"""同步主要指数日线到 index_quotes 表。

数据源：akshare stock_zh_index_daily（新浪源，免费无 Key）。
覆盖总台/指数页所需的上证、深证、创业板、沪深300、中证500、科创50。

用法：
    python webapp/scripts/sync_index_quotes.py            # 增量（近 120 个自然日）
    python webapp/scripts/sync_index_quotes.py --full     # 全量重建
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.utils.env import load_project_env  # noqa: E402

load_project_env()

import akshare as ak  # noqa: E402
import psycopg2  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

INDEXES = [
    ("000001.SH", "上证指数", "sh000001"),
    ("399001.SZ", "深证成指", "sz399001"),
    ("399006.SZ", "创业板指", "sz399006"),
    ("000300.SH", "沪深300", "sh000300"),
    ("000905.SH", "中证500", "sh000905"),
    ("000688.SH", "科创50", "sh000688"),
    ("899050.BJ", "北证50", "sh899050"),
]

DDL = """
CREATE TABLE IF NOT EXISTS index_quotes (
    ts_code   VARCHAR(20) NOT NULL,
    trade_date DATE NOT NULL,
    open  NUMERIC(12,4), high NUMERIC(12,4),
    low   NUMERIC(12,4), close NUMERIC(12,4),
    volume NUMERIC(20,0),
    PRIMARY KEY (ts_code, trade_date)
);
CREATE TABLE IF NOT EXISTS index_basic (
    ts_code VARCHAR(20) PRIMARY KEY,
    name    VARCHAR(50) NOT NULL,
    symbol  VARCHAR(20) NOT NULL
);
"""


def connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def sync(full=False):
    conn = connect()
    cur = conn.cursor()
    for stmt in DDL.strip().split(";"):
        if stmt.strip():
            cur.execute(stmt)
    conn.commit()

    since = None if full else (datetime.now() - timedelta(days=150)).date()
    ok, fail = [], []

    for ts_code, name, symbol in INDEXES:
        optional = ts_code == "899050.BJ"  # 北证50 源不保证存在，失败可跳过
        try:
            df = ak.stock_zh_index_daily(symbol=symbol)
        except Exception as e:  # noqa: BLE001
            print(f"  fetch fail {symbol}: {type(e).__name__} {e}")
            if not optional:
                fail.append(symbol)
            continue
        if df is None or df.empty:
            print(f"  empty {symbol}")
            continue

        df = df.rename(columns={"date": "trade_date", "vol": "volume"})
        df["ts_code"] = ts_code
        df["trade_date"] = pandas_to_date(df["trade_date"])
        if since:
            df = df[df["trade_date"] >= since]
        if df.empty:
            continue

        rows = [
            (
                r.ts_code,
                r.trade_date,
                num(r.open),
                num(r.high),
                num(r.low),
                num(r.close),
                num(r.volume),
            )
            for r in df.itertuples()
        ]
        execute_values(
            cur,
            """
            INSERT INTO index_quotes (ts_code, trade_date, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (ts_code, trade_date) DO UPDATE
              SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                  close=EXCLUDED.close, volume=EXCLUDED.volume
            """,
            rows,
            page_size=1000,
        )
        cur.execute(
            """
            INSERT INTO index_basic (ts_code, name, symbol) VALUES (%s, %s, %s)
            ON CONFLICT (ts_code) DO UPDATE SET name=EXCLUDED.name, symbol=EXCLUDED.symbol
            """,
            (ts_code, name, symbol),
        )
        conn.commit()
        ok.append(f"{ts_code}:{len(rows)}")

    # 创业板50 用创业板指数据不准确，删除历史占位行，避免误导
    cur.execute("DELETE FROM index_basic WHERE name='创业板50'")
    conn.commit()

    cur.execute("SELECT ts_code, MAX(trade_date) FROM index_quotes GROUP BY ts_code ORDER BY ts_code")
    for code, d in cur.fetchall():
        print(f"  {code} -> {d}")
    cur.close()
    conn.close()
    print("synced:", ", ".join(ok))
    if fail:
        print("failed:", ", ".join(fail))


def pandas_to_date(series):
    import pandas as pd

    return pd.to_datetime(series).dt.date


def num(v):
    if v is None:
        return None
    try:
        import pandas as pd

        if pd.isna(v):
            return None
    except Exception:  # noqa: BLE001
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    sync(full="--full" in sys.argv)
