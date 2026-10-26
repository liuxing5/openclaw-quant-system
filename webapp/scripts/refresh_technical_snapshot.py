# -*- coding: utf-8 -*-
"""刷新真实 MACD / 缠论结构表 ``app_technical_snapshot``。

一次 SQL 把每只股票最近约 260 根日线的 high/low/close 聚成数组（约 5,000 行），
在 Python 里逐只算 MACD 与缠论分型/笔/中枢，再批量写回 PostgreSQL。

用法::

    python webapp/scripts/refresh_technical_snapshot.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from psycopg2.extras import execute_values  # noqa: E402

from app.db import db_session, query  # noqa: E402
from app.services import technical  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS app_technical_snapshot (
    trade_date date NOT NULL,
    ts_code varchar(16) NOT NULL,
    dif numeric,
    dea numeric,
    hist numeric,
    macd_cross varchar(16),
    macd_hist_turn varchar(16),
    chan_signal varchar(16),
    chan_trend varchar(16),
    pivot_low numeric,
    pivot_high numeric,
    last_bottom numeric,
    last_top numeric,
    stroke_count int,
    fractal_count int,
    segment_count int,
    segment_trend varchar(16),
    pivot_count int,
    seg_pivot_low numeric,
    seg_pivot_high numeric,
    trend_type varchar(16),
    trend_direction varchar(16),
    buy_point varchar(8),
    sell_point varchar(8),
    updated_at timestamptz DEFAULT now(),
    PRIMARY KEY (trade_date, ts_code)
)
"""

SQL = """
SELECT ts_code,
       array_agg(high ORDER BY trade_date) AS highs,
       array_agg(low ORDER BY trade_date) AS lows,
       array_agg(close ORDER BY trade_date) AS closes
FROM daily_quotes, (SELECT MAX(trade_date) AS day FROM daily_quotes) d
WHERE trade_date >= d.day - INTERVAL '400 days'
GROUP BY ts_code
"""

INSERT = """
INSERT INTO app_technical_snapshot
    (trade_date, ts_code, dif, dea, hist, macd_cross, macd_hist_turn,
     chan_signal, chan_trend, pivot_low, pivot_high, last_bottom, last_top,
     stroke_count, fractal_count, segment_count, segment_trend, pivot_count,
     seg_pivot_low, seg_pivot_high, trend_type, trend_direction, buy_point, sell_point)
VALUES %s
ON CONFLICT (trade_date, ts_code) DO UPDATE SET
    dif = EXCLUDED.dif, dea = EXCLUDED.dea, hist = EXCLUDED.hist,
    macd_cross = EXCLUDED.macd_cross, macd_hist_turn = EXCLUDED.macd_hist_turn,
    chan_signal = EXCLUDED.chan_signal, chan_trend = EXCLUDED.chan_trend,
    pivot_low = EXCLUDED.pivot_low, pivot_high = EXCLUDED.pivot_high,
    last_bottom = EXCLUDED.last_bottom, last_top = EXCLUDED.last_top,
    stroke_count = EXCLUDED.stroke_count, fractal_count = EXCLUDED.fractal_count,
    segment_count = EXCLUDED.segment_count, segment_trend = EXCLUDED.segment_trend,
    pivot_count = EXCLUDED.pivot_count, seg_pivot_low = EXCLUDED.seg_pivot_low,
    seg_pivot_high = EXCLUDED.seg_pivot_high, trend_type = EXCLUDED.trend_type,
    trend_direction = EXCLUDED.trend_direction, buy_point = EXCLUDED.buy_point,
    sell_point = EXCLUDED.sell_point,
    updated_at = now()
"""


def main() -> int:
    t0 = time.time()
    rows = query(SQL)
    day = query("SELECT MAX(trade_date) AS day FROM daily_quotes")[0]["day"]
    print(f"loaded {len(rows)} stocks, day={day}, {time.time() - t0:.1f}s", flush=True)
    values = []
    for r in rows:
        highs = [float(x) for x in (r["highs"] or []) if x is not None]
        lows = [float(x) for x in (r["lows"] or []) if x is not None]
        closes = [float(x) for x in (r["closes"] or []) if x is not None]
        if len(closes) < 26:
            continue
        m = technical.macd(closes)
        c = technical.chan(highs, lows, closes, m)
        values.append(
            (
                day,
                r["ts_code"],
                m["dif"], m["dea"], m["hist"], m["cross"], m["hist_turn"],
                c["chan_signal"], c["chan_trend"], c["pivot_low"], c["pivot_high"],
                c["last_bottom"], c["last_top"], c["stroke_count"], c["fractal_count"],
                c["segment_count"], c["segment_trend"], c["pivot_count"],
                c["seg_pivot_low"], c["seg_pivot_high"], c["trend_type"],
                c["trend_direction"], c["buy_point"], c["sell_point"],
            )
        )
    print(f"computed {len(values)} rows in {time.time() - t0:.1f}s", flush=True)
    with db_session() as conn:
        cur = conn.cursor()
        try:
            cur.execute(DDL)
            for name, typ in (
                ("segment_count", "int"),
                ("segment_trend", "varchar(16)"),
                ("pivot_count", "int"),
                ("seg_pivot_low", "numeric"),
                ("seg_pivot_high", "numeric"),
                ("trend_type", "varchar(16)"),
                ("trend_direction", "varchar(16)"),
                ("buy_point", "varchar(8)"),
                ("sell_point", "varchar(8)"),
            ):
                cur.execute(f"ALTER TABLE app_technical_snapshot ADD COLUMN IF NOT EXISTS {name} {typ}")
            execute_values(cur, INSERT, values, page_size=500)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    print(f"done {len(values)} rows, {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
