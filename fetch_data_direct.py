"""
尝试直接连接Supabase数据库（绕过pooler）
"""
import psycopg2
import time
import pickle
import pandas as pd
from psycopg2.extras import RealDictCursor

# 直接连接（不使用pooler）
DB_URL_DIRECT = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@db.qoakbxswwjqfsgbcgepr.supabase.co:5432/postgres"
CACHE_FILE = r'd:\pythonProject\openclaw-quant-system\data_cache.pkl'

print("尝试直接连接数据库...")
for attempt in range(3):
    try:
        conn = psycopg2.connect(DB_URL_DIRECT, connect_timeout=30, sslmode='require')
        conn.autocommit = True
        print(f"✓ 直接连接成功 (尝试 {attempt+1})")
        
        print("查询数据...")
        t0 = time.time()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover_rate
            FROM daily_quotes 
            WHERE trade_date >= '2025-04-01' AND trade_date <= '2026-05-25'
              AND pct_chg IS NOT NULL AND amount IS NOT NULL AND turnover_rate IS NOT NULL
            ORDER BY ts_code, trade_date
        """)
        rows = cur.fetchall()
        print(f"✓ 获取 {len(rows)} 条数据, 耗时 {time.time()-t0:.1f}s")
        
        print("处理数据...")
        df = pd.DataFrame(rows)
        for c in ["open","high","low","close","volume","amount","pct_chg","turnover_rate"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close","pct_chg","amount","turnover_rate"])
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
        
        print(f"保存缓存: {len(df)} 条, {df['ts_code'].nunique()} 只股票...")
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(df, f)
        print("✓ 缓存保存成功!")
        
        cur.close()
        conn.close()
        break
    except Exception as e:
        err = str(e)
        if "timeout" in err.lower() or "timed out" in err.lower():
            print(f"直接连接超时 (尝试 {attempt+1})")
            if attempt == 0:
                print("尝试使用pooler...")
                break
        else:
            print(f"错误: {err[:120]}")
            time.sleep(5)
else:
    print("✗ 所有尝试都失败了")
