import psycopg2
from psycopg2.extras import RealDictCursor
import sys

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"

results = []

def test(name, func):
    results.append(f"\n{name}")
    try:
        func()
    except Exception as e:
        results.append(f"  FAILED: {e}")

def test1():
    conn = psycopg2.connect(DB_URL, connect_timeout=60, sslmode='require')
    results.append("  Connected!")
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT 1 as test")
    results.append(f"  Query OK: {cur.fetchone()}")
    cur.close()
    conn.close()

def test2():
    conn = psycopg2.connect(DB_URL, connect_timeout=60, sslmode='prefer')
    results.append("  Connected!")
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT 1 as test")
    results.append(f"  Query OK: {cur.fetchone()}")
    cur.close()
    conn.close()

def test3():
    conn = psycopg2.connect(DB_URL, connect_timeout=60, sslmode='disable')
    results.append("  Connected!")
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT 1 as test")
    results.append(f"  Query OK: {cur.fetchone()}")
    cur.close()
    conn.close()

def test4():
    conn = psycopg2.connect(DB_URL, connect_timeout=60, sslmode='require')
    results.append("  Connected!")
    cur = conn.cursor("test_cursor", cursor_factory=RealDictCursor, withhold=True)
    cur.execute("SELECT 1 as test")
    rows = cur.fetchmany(1)
    results.append(f"  Query OK: {rows}")
    cur.close()
    conn.close()

def test5():
    conn = psycopg2.connect(DB_URL, connect_timeout=60, sslmode='require')
    results.append("  Connected!")
    cur = conn.cursor("test_cursor", cursor_factory=RealDictCursor, withhold=True)
    cur.execute("SELECT COUNT(*) as cnt FROM daily_quotes WHERE trade_date >= '2026-05-01' AND trade_date <= '2026-05-25'")
    rows = cur.fetchmany(1)
    results.append(f"  Query OK: {rows}")
    cur.close()
    conn.close()

test("Test 1: sslmode='require' + normal cursor", test1)
test("Test 2: sslmode='prefer' + normal cursor", test2)
test("Test 3: sslmode='disable' + normal cursor", test3)
test("Test 4: sslmode='require' + server-side cursor", test4)
test("Test 5: sslmode='require' + server-side cursor (real query)", test5)

results.append("\nDone!")

output = "\n".join(results)
print(output)

with open('ssl_test_result.txt', 'w', encoding='utf-8') as f:
    f.write(output)
