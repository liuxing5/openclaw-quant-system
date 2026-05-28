"""
极简数据获取 - 使用极小批次和SSL重试
"""
import psycopg2
import time
import pickle
import pandas as pd
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"
CACHE_FILE = r'd:\pythonProject\openclaw-quant-system\data_cache.pkl'

# 按周分批 - 极小批次
def get_weeks(start_str, end_str):
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")
    weeks = []
    current = start
    while current <= end:
        week_end = min(current + timedelta(days=6), end)
        weeks.append((current.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d")))
        current = week_end + timedelta(days=1)
    return weeks

weeks = get_weeks("2025-04-01", "2026-05-25")
print(f"共 {len(weeks)} 个周批次")

all_dfs = []
total = 0
success = 0
failed = 0

for idx, (ws, we) in enumerate(weeks):
    print(f"[{idx+1}/{len(weeks)}] {ws}~{we}...", end=" ", flush=True)
    
    for attempt in range(8):
        conn = None
        try:
            conn = psycopg2.connect(DB_URL, connect_timeout=15, sslmode='require')
            conn.autocommit = True
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover_rate
                FROM daily_quotes 
                WHERE trade_date >= %s AND trade_date <= %s
                  AND pct_chg IS NOT NULL AND amount IS NOT NULL AND turnover_rate IS NOT NULL
            """, (ws, we))
            rows = cur.fetchall()
            cur.close()
            conn.close()
            conn = None
            
            if rows:
                df = pd.DataFrame(rows)
                for c in ["open","high","low","close","volume","amount","pct_chg","turnover_rate"]:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                df["trade_date"] = pd.to_datetime(df["trade_date"])
                all_dfs.append(df)
                total += len(rows)
                success += 1
            
            print(f"✓ {len(rows)} (累计 {total})")
            time.sleep(2)  # 等待连接池释放
            break
            
        except psycopg2.OperationalError as e:
            if conn:
                try: conn.close()
                except: pass
            conn = None
            err = str(e)
            if "SSL" in err:
                print(f"\n  SSL错误, 等5s重试({attempt+1})...", end=" ", flush=True)
                time.sleep(5)
            elif "max clients" in err:
                print(f"\n  池满, 等15s重试({attempt+1})...", end=" ", flush=True)
                time.sleep(15)
            else:
                print(f"\n  连接错误: {err[:80]}")
                time.sleep(5)
        except Exception as e:
            if conn:
                try: conn.close()
                except: pass
            conn = None
            print(f"\n  错误: {str(e)[:80]}")
            time.sleep(5)
    else:
        print("✗ 失败")
        failed += 1

if all_dfs:
    df = pd.concat(all_dfs, ignore_index=True)
    df = df.dropna(subset=["close","pct_chg","amount","turnover_rate"])
    df = df.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
    
    print(f"\n保存: {len(df)} 条, {df['ts_code'].nunique()} 只股票")
    print(f"成功: {success}/{len(weeks)}, 失败: {failed}/{len(weeks)}")
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(df, f)
    print("✓ 缓存保存成功!")
else:
    print("\n✗ 没有获取到数据")
