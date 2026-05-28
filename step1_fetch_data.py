"""
使用6543端口分批查询 - 按周分批，每次查询后立即关闭连接
"""
import psycopg2
import time
import pickle
import pandas as pd
from psycopg2.extras import RealDictCursor

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"
CACHE_FILE = r'd:\pythonProject\openclaw-quant-system\data_cache.pkl'

# 按周分批
date_ranges = [
    ('2025-04-01', '2025-04-07'),
    ('2025-04-08', '2025-04-14'),
    ('2025-04-15', '2025-04-21'),
    ('2025-04-22', '2025-04-30'),
    ('2025-05-01', '2025-05-07'),
    ('2025-05-08', '2025-05-14'),
    ('2025-05-15', '2025-05-21'),
    ('2025-05-22', '2025-05-31'),
    ('2025-06-01', '2025-06-07'),
    ('2025-06-08', '2025-06-14'),
    ('2025-06-15', '2025-06-21'),
    ('2025-06-22', '2025-06-30'),
    ('2025-07-01', '2025-07-07'),
    ('2025-07-08', '2025-07-14'),
    ('2025-07-15', '2025-07-21'),
    ('2025-07-22', '2025-07-31'),
    ('2025-08-01', '2025-08-07'),
    ('2025-08-08', '2025-08-14'),
    ('2025-08-15', '2025-08-21'),
    ('2025-08-22', '2025-08-31'),
    ('2025-09-01', '2025-09-07'),
    ('2025-09-08', '2025-09-14'),
    ('2025-09-15', '2025-09-21'),
    ('2025-09-22', '2025-09-30'),
    ('2025-10-01', '2025-10-07'),
    ('2025-10-08', '2025-10-14'),
    ('2025-10-15', '2025-10-21'),
    ('2025-10-22', '2025-10-31'),
    ('2025-11-01', '2025-11-07'),
    ('2025-11-08', '2025-11-14'),
    ('2025-11-15', '2025-11-21'),
    ('2025-11-22', '2025-11-30'),
    ('2025-12-01', '2025-12-07'),
    ('2025-12-08', '2025-12-14'),
    ('2025-12-15', '2025-12-21'),
    ('2025-12-22', '2025-12-31'),
    ('2026-01-01', '2026-01-07'),
    ('2026-01-08', '2026-01-14'),
    ('2026-01-15', '2026-01-21'),
    ('2026-01-22', '2026-01-31'),
    ('2026-02-01', '2026-02-07'),
    ('2026-02-08', '2026-02-14'),
    ('2026-02-15', '2026-02-21'),
    ('2026-02-22', '2026-02-28'),
    ('2026-03-01', '2026-03-07'),
    ('2026-03-08', '2026-03-14'),
    ('2026-03-15', '2026-03-21'),
    ('2026-03-22', '2026-03-31'),
    ('2026-04-01', '2026-04-07'),
    ('2026-04-08', '2026-04-14'),
    ('2026-04-15', '2026-04-21'),
    ('2026-04-22', '2026-04-30'),
    ('2026-05-01', '2026-05-07'),
    ('2026-05-08', '2026-05-14'),
    ('2026-05-15', '2026-05-21'),
    ('2026-05-22', '2026-05-25'),
]

all_dfs = []
total = 0
success_count = 0

for idx, (start, end) in enumerate(date_ranges):
    print(f"[{idx+1}/{len(date_ranges)}] {start}~{end}...", end=" ", flush=True)
    for attempt in range(5):
        try:
            conn = psycopg2.connect(DB_URL, connect_timeout=10, sslmode='require')
            conn.autocommit = True
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SET statement_timeout = '60000'")
            cur.execute("""
                SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover_rate
                FROM daily_quotes 
                WHERE trade_date >= %s AND trade_date <= %s
                  AND pct_chg IS NOT NULL AND amount IS NOT NULL AND turnover_rate IS NOT NULL
            """, (start, end))
            rows = cur.fetchall()
            cur.close()
            conn.close()
            
            if rows:
                df = pd.DataFrame(rows)
                for c in ["open","high","low","close","volume","amount","pct_chg","turnover_rate"]:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                df["trade_date"] = pd.to_datetime(df["trade_date"])
                all_dfs.append(df)
                total += len(df)
                success_count += 1
            print(f"✓ {len(rows) if rows else 0} (累计 {total})")
            time.sleep(1)
            break
        except Exception as e:
            err = str(e)
            if "max clients" in err:
                print(f"\n    池满, 等10s...", end=" ", flush=True)
                time.sleep(10)
            elif "SSL" in err:
                print(f"\n    SSL, 等3s...", end=" ", flush=True)
                time.sleep(3)
            elif "canceling" in err:
                print(f"\n    超时, 重试...", end=" ", flush=True)
                time.sleep(3)
            else:
                print(f"\n    {err[:80]}")
                time.sleep(3)
    else:
        print("✗")

if all_dfs:
    df = pd.concat(all_dfs, ignore_index=True)
    df = df.dropna(subset=["close","pct_chg","amount","turnover_rate"])
    df = df.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
    
    print(f"\n保存: {len(df)} 条, {df['ts_code'].nunique()} 只, {success_count}/{len(date_ranges)} 批次成功")
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(df, f)
    print("✓ 缓存成功!")
else:
    print("\n✗ 无数据")
