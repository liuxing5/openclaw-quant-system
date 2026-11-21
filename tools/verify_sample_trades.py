"""独立复核：用 baostock 重新拉 5m + Supabase 日线，核对大额盈利笔的买卖价。"""
import baostock as bs
import pandas as pd
import psycopg2

DSN = ("postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl"
       "@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres")
SAMPLES = [
    ("002149.SZ", "2025-02-28", "2025-03-03"),
    ("300791.SZ", "2025-05-22", "2025-05-23"),
    ("002978.SZ", "2026-02-26", "2026-02-27"),
    ("300870.SZ", "2025-02-18", "2025-02-19"),
    ("688041.SH", "2025-02-28", "2025-03-03"),
]


def bs_code(ts):
    return ("sh." if ts.endswith(".SH") else "sz.") + ts.split(".")[0]


def db_daily(ts, start, end):
    conn = psycopg2.connect(DSN, connect_timeout=30)
    try:
        cur = conn.cursor()
        cur.execute("""select trade_date, open::float8, high::float8, low::float8, close::float8,
                       pre_close::float8, pct_chg::float8
                       from daily_quotes where ts_code=%s and trade_date between %s and %s
                       order by trade_date""", (ts, start, end))
        return cur.fetchall()
    finally:
        conn.close()


bs.login()
for ts, start, end in SAMPLES:
    print(f"\n===== {ts} {start} -> {end} =====")
    for r in db_daily(ts, start, end):
        print("DB日线", r)
    rs = bs.query_history_k_data_plus(bs_code(ts), "date,time,open,high,low,close,volume,amount",
                                     start_date=start, end_date=end, frequency="5", adjustflag="2")
    bars = []
    while rs.error_code == "0" and rs.next():
        r = rs.get_row_data()
        bars.append(r)
    # 只打印 T 14:40-15:00 和 T+1 09:30-10:05
    for r in bars:
        t = r[1][8:12]
        if (r[0] == start and t >= "1440") or (r[0] == end and "0925" <= t <= "1005"):
            print("5m", r[0], t, "O", r[2], "H", r[3], "L", r[4], "C", r[5])
bs.logout()
