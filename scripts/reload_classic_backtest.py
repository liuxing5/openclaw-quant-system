# -*- coding: utf-8 -*-
"""从已生成的 JSON 报告批量回填回测表（避免重复调用 baostock）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import psycopg2
from psycopg2.extras import execute_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webapp" / "backend"))
sys.path.insert(0, str(ROOT))

if not os.getenv("DATABASE_URL") and os.getenv("POSTGRES_HOST"):
    _u = quote(os.getenv("POSTGRES_USER", ""), safe="")
    _p = quote(os.getenv("POSTGRES_PASSWORD", ""), safe="")
    os.environ["DATABASE_URL"] = (
        f"postgresql://{_u}:{_p}@{os.getenv('POSTGRES_HOST')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'postgres')}?sslmode=require"
    )
    os.environ["WEBAPP_DATABASE_URL"] = os.environ["DATABASE_URL"]

from scripts.backtest_overnight_classic import _init_schema  # noqa: E402

TRADE_COLS = [
    "signal_date", "ts_code", "stock_name", "daily_pct", "turnover_rate", "cap_est",
    "entry_price", "vwap", "new_high", "pullback", "above_vwap", "stronger", "confirmed",
    "next_trade_date", "max_return", "close_1030", "ret_1030", "net_ret_1030",
    "hit_1", "hit_15", "hit_2", "hit_25", "hit_3",
    "ret_1", "ret_15", "ret_2", "ret_25", "ret_3",
    "net_ret_1", "net_ret_15", "net_ret_2", "net_ret_25", "net_ret_3",
]


def main():
    data = json.loads((ROOT / "reports/overnight_classic_backtest.json").read_text(encoding="utf-8"))
    rows = data["rows"]
    daily = data["daily"]
    _init_schema()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("DELETE FROM classic_backtest_10m")
    cur.execute("DELETE FROM classic_backtest_trades")
    cur.execute("DELETE FROM classic_backtest_daily")

    values = [tuple(r.get(c) for c in TRADE_COLS) for r in rows]
    update = ",".join(f"{c}=EXCLUDED.{c}" for c in TRADE_COLS[2:])
    execute_values(
        cur,
        "INSERT INTO classic_backtest_trades (" + ",".join(TRADE_COLS) + ") VALUES %s "
        "ON CONFLICT (signal_date, ts_code) DO UPDATE SET " + update,
        values,
        page_size=500,
    )

    bars = []
    for r in rows:
        for b in r.get("bars_10m") or []:
            bars.append(
                (
                    r["signal_date"], r["ts_code"], b["bar_time"],
                    b["open"], b["high"], b["low"], b["close"], b["volume"], b["amount"],
                )
            )
    execute_values(
        cur,
        "INSERT INTO classic_backtest_10m "
        "(signal_date,ts_code,bar_time,open,high,low,close,volume,amount) VALUES %s "
        "ON CONFLICT (signal_date,ts_code,bar_time) DO UPDATE SET "
        "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,"
        "volume=EXCLUDED.volume,amount=EXCLUDED.amount",
        bars,
        page_size=1000,
    )
    execute_values(
        cur,
        "INSERT INTO classic_backtest_daily "
        "(trade_date,daily_candidates,confirmed,next_trade_date) VALUES %s "
        "ON CONFLICT (trade_date) DO UPDATE SET daily_candidates=EXCLUDED.daily_candidates,"
        "confirmed=EXCLUDED.confirmed,next_trade_date=EXCLUDED.next_trade_date,updated_at=now()",
        [(d["trade_date"], d["daily_candidates"], d["confirmed"], d["next_trade_date"]) for d in daily],
    )
    conn.commit()
    print(f"trades={len(rows)} bars={len(bars)} daily={len(daily)}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
