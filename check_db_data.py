#!/usr/bin/env python3
"""检查数据库中的可用数据"""
import psycopg2

conn = psycopg2.connect(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=5432,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
    connect_timeout=30,
)
cur = conn.cursor()

# 检查stock_fundamentals表结构
cur.execute("""
    SELECT column_name FROM information_schema.columns 
    WHERE table_name = 'stock_fundamentals' ORDER BY ordinal_position
""")
print(f"stock_fundamentals columns: {[r[0] for r in cur.fetchall()]}")

# 检查stock_fundamentals数据量
cur.execute("SELECT count(*) FROM stock_fundamentals")
print(f"stock_fundamentals records: {cur.fetchone()[0]}")

# 检查stock_basic_info表结构
cur.execute("""
    SELECT column_name FROM information_schema.columns 
    WHERE table_name = 'stock_basic_info' ORDER BY ordinal_position
""")
print(f"stock_basic_info columns: {[r[0] for r in cur.fetchall()]}")

# 检查stock_basic_info数据量
cur.execute("SELECT count(*) FROM stock_basic_info")
print(f"stock_basic_info records: {cur.fetchone()[0]}")

# 检查daily_quotes日期范围
cur.execute("SELECT min(trade_date), max(trade_date) FROM daily_quotes")
dates = cur.fetchone()
print(f"daily_quotes date range: {dates[0]} ~ {dates[1]}")

# 检查每日股票数量
cur.execute("""
    SELECT trade_date, count(*) FROM daily_quotes 
    WHERE trade_date >= '2026-04-01' 
    GROUP BY trade_date ORDER BY trade_date LIMIT 10
""")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]} stocks")

# 检查concept_board_quotes
cur.execute("""
    SELECT column_name FROM information_schema.columns 
    WHERE table_name = 'concept_board_quotes' ORDER BY ordinal_position
""")
print(f"concept_board_quotes columns: {[r[0] for r in cur.fetchall()]}")

cur.execute("SELECT count(*) FROM concept_board_quotes")
print(f"concept_board_quotes records: {cur.fetchone()[0]}")

conn.close()
