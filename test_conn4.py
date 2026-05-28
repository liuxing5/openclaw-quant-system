import psycopg2
import time
import sys

# 尝试直接连接（非pooler）
configs = [
    # 直接连接
    {"host": "db.qoakbxswwjqfsgbcgepr.supabase.co", "port": 5432, "dbname": "postgres", "user": "postgres.qoakbxswwjqfsgbcgepr", "password": "wYFBB91zViSrk2vl", "sslmode": "require", "connect_timeout": 15},
    # pooler 5432
    {"host": "aws-1-ap-northeast-1.pooler.supabase.com", "port": 5432, "dbname": "postgres", "user": "postgres.qoakbxswwjqfsgbcgepr", "password": "wYFBB91zViSrk2vl", "sslmode": "require", "connect_timeout": 15},
    # pooler 6543
    {"host": "aws-1-ap-northeast-1.pooler.supabase.com", "port": 6543, "dbname": "postgres", "user": "postgres.qoakbxswwjqfsgbcgepr", "password": "wYFBB91zViSrk2vl", "sslmode": "require", "connect_timeout": 15},
]

for i, cfg in enumerate(configs):
    print(f"\n尝试配置 {i+1}: {cfg['host']}:{cfg['port']}")
    for attempt in range(5):
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
                print(f"  尝试 {attempt+1}: 连接池满, 等待20s...")
                time.sleep(20)
            elif "SSL" in err:
                print(f"  尝试 {attempt+1}: SSL错误, 等待5s...")
                time.sleep(5)
            elif "could not translate" in err or "getaddrinfo" in err:
                print(f"  尝试 {attempt+1}: 无法解析主机名")
                break
            else:
                print(f"  尝试 {attempt+1}: {err[:120]}")
                time.sleep(10)

print("\n所有配置都失败了")
sys.exit(1)
