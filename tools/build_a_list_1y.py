"""构建 2025-10 ~ 2026-11 全市场 A 级名单（日线口径）。"""
import sys

import pandas as pd

sys.path.insert(0, ".")
from core.utils.env import load_project_env
from core.db.connection import get_db_fresh

START, END = "2025-06-01", "2026-11-21"

SQL = """
WITH d AS (
  SELECT q.ts_code, q.trade_date, q.open, q.high, q.low, q.close, q.pre_close,
         q.pct_chg, q.amount, q.turnover_rate, q.circulating_market_cap, q.total_market_cap,
         lag(q.close) OVER w AS prev_close,
         avg(q.close) OVER (PARTITION BY q.ts_code ORDER BY q.trade_date
                            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prev_ma20,
         avg(q.close) OVER (PARTITION BY q.ts_code ORDER BY q.trade_date
                            ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING) AS prev_ma60,
         lag(q.close, 1) OVER w / NULLIF(lag(q.close, 21) OVER w, 0) - 1 AS prev_ret20,
         lag(q.close, 1) OVER w / NULLIF(lag(q.close, 6)  OVER w, 0) - 1 AS prev_ret5,
         sum(CASE WHEN q.pct_chg >= 9.5 THEN 1 ELSE 0 END)
           OVER (PARTITION BY q.ts_code ORDER BY q.trade_date
                 ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS lu20,
         avg(q.amount) OVER (PARTITION BY q.ts_code ORDER BY q.trade_date
                             ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS amount_ma20
  FROM daily_quotes q
  WHERE q.trade_date BETWEEN %s AND %s
  WINDOW w AS (PARTITION BY q.ts_code ORDER BY q.trade_date)
)
SELECT d.*, (d.close - d.low) / NULLIF(d.high - d.low, 0) AS pos,
       d.amount / NULLIF(d.amount_ma20, 0) AS amount_ratio
FROM d
WHERE d.trade_date >= '2025-10-01'
  AND d.pct_chg BETWEEN -7 AND -2
  AND d.turnover_rate BETWEEN 1.5 AND 8
  AND d.amount >= 1e8
  AND COALESCE(d.circulating_market_cap, d.total_market_cap,
               d.amount / NULLIF(d.turnover_rate / 100.0, 0)) / 1e8 BETWEEN 50 AND 2000
  AND (d.close - d.low) / NULLIF(d.high - d.low, 0) < 0.20
  AND d.prev_close > d.prev_ma20 AND d.prev_ma20 > d.prev_ma60
  AND d.prev_ret20 > 0.03 AND d.prev_ret5 > -0.02
  AND COALESCE(d.lu20, 0) <= 1
  AND d.amount / NULLIF(d.amount_ma20, 0) >= 0.8
ORDER BY d.trade_date, d.ts_code
"""


def main():
    load_project_env()
    conn = get_db_fresh()
    try:
        df = pd.read_sql(SQL, conn, params=(START, END))
        b = pd.read_sql("""
            WITH d AS (SELECT ts_code, trade_date, close,
                       avg(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) ma20
                       FROM daily_quotes WHERE trade_date BETWEEN '2025-09-01' AND '2026-11-21')
            SELECT trade_date, avg(CASE WHEN close > ma20 THEN 1.0 ELSE 0.0 END) AS b20
            FROM d GROUP BY trade_date ORDER BY trade_date
        """, conn)
    finally:
        conn.close()
    print("raw A", len(df), "days", df.trade_date.nunique())
    df = df.merge(b, on="trade_date", how="left")
    df["b20"] = pd.to_numeric(df["b20"], errors="coerce")
    a = df[(df.b20 >= 0.20) & (df.b20 <= 0.70)].copy()
    a["ym"] = pd.to_datetime(a.trade_date).dt.strftime("%Y-%m")
    print("A after breadth", len(a), "days", a.trade_date.nunique(),
          "avg/day", round(len(a) / max(a.trade_date.nunique(), 1), 2))
    print(a.groupby("ym").size().to_string())
    a.to_parquet("data/research/a_list_1y.parquet", index=False)
    print("saved data/research/a_list_1y.parquet")


if __name__ == "__main__":
    main()
