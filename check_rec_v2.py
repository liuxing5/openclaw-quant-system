import psycopg2
from psycopg2.extras import RealDictCursor
import time
from datetime import datetime

print(f"Script starting at {datetime.now()}", flush=True)

DB_HOST = 'aws-1-ap-northeast-1.pooler.supabase.com'
DB_PORT = 6543
DB_USER = 'postgres.qoakbxswwjqfsgbcgepr'
DB_PASS = 'wYFBB91zViSrk2vl'
DB_NAME = 'postgres'

print(f"Connecting to {DB_HOST}:{DB_PORT} as {DB_USER}", flush=True)

conn = None
for attempt in range(15):
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER,
            password=DB_PASS, dbname=DB_NAME,
            sslmode='require', connect_timeout=30,
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        print(f"Connected successfully on attempt {attempt+1}", flush=True)
        break
    except Exception as e:
        err = str(e)
        if 'max clients' in err:
            wait = 15 * (attempt + 1)
            print(f"Pool full, waiting {wait}s (attempt {attempt+1}/15)...", flush=True)
            time.sleep(wait)
        else:
            print(f"Error: {e}", flush=True)
            time.sleep(5)

if not conn:
    print("FAILED to connect after all retries")
    exit(1)

cur = conn.cursor(cursor_factory=RealDictCursor)

cur.execute("""
    SELECT snapshot_date, source, run_mode, COUNT(*) as cnt
    FROM daily_candidates
    WHERE selected = true
    GROUP BY snapshot_date, source, run_mode
    ORDER BY snapshot_date DESC
    LIMIT 10
""")
recent = cur.fetchall()

print("=" * 80)
print("Recent recommendations:")
print("=" * 80)
for r in recent:
    print(f"  {r['snapshot_date']} | {r['source']} | {r['run_mode']} | {r['cnt']} stocks")

if recent:
    latest_date = recent[0]['snapshot_date']
    latest_source = recent[0]['source']
    latest_mode = recent[0]['run_mode']
    
    print(f"\nQuerying latest: {latest_date} ({latest_source}, {latest_mode})")
    print("=" * 80)
    
    cur.execute("""
        SELECT ts_code, stock_name, final_score, entry_low, entry_high
        FROM daily_candidates
        WHERE snapshot_date = %s AND source = %s AND run_mode = %s AND selected = true
        ORDER BY final_score DESC NULLS LAST
    """, (latest_date, latest_source, latest_mode))
    
    stocks = cur.fetchall()
    print(f"Found {len(stocks)} recommended stocks")
    
    if stocks:
        codes = [s['ts_code'] for s in stocks]
        ph = ','.join(['%s'] * len(codes))
        cur.execute(f"""
            SELECT dq.ts_code, dq.trade_date, dq.close
            FROM daily_quotes dq
            INNER JOIN (
                SELECT ts_code, MAX(trade_date) as md
                FROM daily_quotes WHERE ts_code IN ({ph})
                GROUP BY ts_code
            ) latest ON dq.ts_code = latest.ts_code AND dq.trade_date = latest.md
        """, codes)
        
        quotes = {r['ts_code']: r for r in cur.fetchall()}
        
        print(f"\n{'Code':<12} {'Name':<10} {'RecPrice':<10} {'NowPrice':<10} {'Date':<12} {'Change%':<10} Status")
        print("-" * 80)
        
        gains = 0
        losses = 0
        total_chg = 0
        
        for s in stocks:
            code = s['ts_code']
            name = s['stock_name'] or code
            el = s['entry_low']
            eh = s['entry_high']
            
            if el and eh:
                rec_price = float((el + eh) / 2)
            elif el:
                rec_price = float(el)
            elif eh:
                rec_price = float(eh)
            else:
                rec_price = None
            
            q = quotes.get(code)
            
            if q and rec_price and rec_price > 0:
                now = float(q['close'])
                dt = q['trade_date']
                chg = (now - rec_price) / rec_price * 100
                chg_str = f"{chg:+.2f}%"
                status = "UP" if chg > 0 else ("DOWN" if chg < 0 else "FLAT")
                if chg > 0: gains += 1
                elif chg < 0: losses += 1
                total_chg += chg
            else:
                now = None
                dt = q['trade_date'] if q else None
                chg_str = "N/A"
                status = "NO_DATA"
            
            rp = f"{rec_price:.3f}" if rec_price else "N/A"
            np = f"{now:.3f}" if now else "N/A"
            print(f"{code:<12} {name:<10} {rp:<10} {np:<10} {str(dt):<12} {chg_str:<10} {status}")
        
        print("-" * 80)
        n = len(stocks)
        avg = total_chg / n if n else 0
        print(f"Total: {n} | Up: {gains} | Down: {losses} | Avg: {avg:+.2f}%")

cur.close()
conn.close()
print("\nDone.")
