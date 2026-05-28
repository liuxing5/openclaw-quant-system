import psycopg2
import time
import sys

# 尝试不同的连接参数
configs = [
    {"host": "aws-1-ap-northeast-1.pooler.supabase.com", "port": 5432, "dbname": "postgres", "user": "postgres.qoakbxswwjqfsgbcgepr", "password": "wYFBB91zViSrk2vl", "sslmode": "require", "connect_timeout": 10},
    {"host": "aws-1-ap-northeast-1.pooler.supabase.com", "port": 6543, "dbname": "postgres", "user": "postgres.qoakbxswwjqfsgbcgepr", "password": "wYFBB91zViSrk2vl", "sslmode": "require", "connect_timeout": 10},
]

for i, cfg in enumerate(configs):
    print(f"\n尝试配置 {i+1}: {cfg['host']}:{cfg['port']} (sslmode={cfg['sslmode']})")
    for attempt in range(10):
        try:
            conn = psycopg2.connect(**cfg)
            cur = conn.cursor()
            cur.execute("SELECT 1")
            print(f"  ✓ 连接成功!")
            cur.execute("SELECT COUNT(*) FROM daily_quotes WHERE trade_date >= '2025-04-01'")
            print(f"  数据量: {cur.fetchone()[0]}")
            conn.close()
            sys.exit(0)
        except Exception as e:
            err = str(e)
            if "max clients" in err:
                print(f"  尝试 {attempt+1}: 连接池满, 等待15s...")
                time.sleep(15)
            elif "SSL" in err:
                print(f"  尝试 {attempt+1}: SSL错误, 等待5s...")
                time.sleep(5)
            else:
                print(f"  尝试 {attempt+1}: {err[:100]}")
                time.sleep(5)

print("\n所有配置都失败了")
sys.exit(1)
