import psycopg2
from psycopg2.extras import RealDictCursor
import time, sys

DB_HOST = 'aws-1-ap-northeast-1.pooler.supabase.com'
DB_PORT = 5432
DB_USER = 'postgres.qoakbxswwjqfsgbcgepr'
DB_PASS = 'wYFBB91zViSrk2vl'
DB_NAME = 'postgres'

def get_conn():
    for i in range(8):
        try:
            c = psycopg2.connect(
                host=DB_HOST, port=DB_PORT, user=DB_USER,
                password=DB_PASS, dbname=DB_NAME,
                sslmode='require', connect_timeout=15,
                options='-c statement_timeout=30000',
            )
            cur = c.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return c
        except Exception as e:
            err = str(e)
            if 'max clients' in err:
                w = 10 * (i + 1)
                print(f"Pool full, wait {w}s ({i+1}/8)...", flush=True)
                time.sleep(w)
            elif 'SSL' in err:
                print(f"SSL error, retry ({i+1}/8)...", flush=True)
                time.sleep(3)
            else:
                print(f"Error: {err}", flush=True)
                time.sleep(3)
    raise Exception("Connect failed")

def run_query(sql, params=None):
    for i in range(3):
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return rows
        except Exception as e:
            print(f"Query retry {i+1}: {e}", flush=True)
            time.sleep(2)
    raise Exception("Query failed")

print("Starting...", flush=True)

try:
    recent = run_query("""
        SELECT snapshot_date, source, run_mode, COUNT(*) as cnt
        FROM daily_candidates
        WHERE selected = true
        GROUP BY snapshot_date, source, run_mode
        ORDER BY snapshot_date DESC
        LIMIT 10
    """)
    
    print("=" * 80, flush=True)
    print("Recent recommendations:", flush=True)
    print("=" * 80, flush=True)
    for r in recent:
        print(f"  {r['snapshot_date']} | {r['source']} | {r['run_mode']} | {r['cnt']}", flush=True)
    
    if recent:
        ld, ls, lm = recent[0]['snapshot_date'], recent[0]['source'], recent[0]['run_mode']
        print(f"\nLatest: {ld} ({ls}, {lm})", flush=True)
        print("=" * 80, flush=True)
        
        stocks = run_query("""
            SELECT ts_code, stock_name, entry_low, entry_high
            FROM daily_candidates
            WHERE snapshot_date = %s AND source = %s AND run_mode = %s AND selected = true
            ORDER BY final_score DESC NULLS LAST
        """, (ld, ls, lm))
        
        print(f"Found {len(stocks)} stocks", flush=True)
        
        if stocks:
            codes = [s['ts_code'] for s in stocks]
            all_quotes = {}
            bs = 10
            for i in range(0, len(codes), bs):
                batch = codes[i:i+bs]
                ph = ','.join(['%s'] * len(batch))
                rows = run_query(f"""
                    SELECT dq.ts_code, dq.trade_date, dq.close
                    FROM daily_quotes dq
                    INNER JOIN (
                        SELECT ts_code, MAX(trade_date) as md
                        FROM daily_quotes WHERE ts_code IN ({ph})
                        GROUP BY ts_code
                    ) latest ON dq.ts_code = latest.ts_code AND dq.trade_date = latest.md
                """, batch)
                for r in rows:
                    all_quotes[r['ts_code']] = r
            
            print(f"\n{'Code':<12} {'Name':<10} {'Rec':<8} {'Now':<8} {'Date':<12} {'Chg%':<10} Status", flush=True)
            print("-" * 80, flush=True)
            
            g = l = tc = 0
            for s in stocks:
                code = s['ts_code']
                name = (s['stock_name'] or '')[:10]
                el, eh = s['entry_low'], s['entry_high']
                rp = float((el + eh) / 2) if el and eh else (float(el) if el else (float(eh) if eh else None))
                q = all_quotes.get(code)
                if q and rp and rp > 0:
                    now = float(q['close'])
                    chg = (now - rp) / rp * 100
                    cs = f"{chg:+.2f}%"
                    st = "UP" if chg > 0 else ("DOWN" if chg < 0 else "FLAT")
                    if chg > 0: g += 1
                    elif chg < 0: l += 1
                    tc += chg
                else:
                    now = None; cs = "N/A"; st = "NO_DATA"
                print(f"{code:<12} {name:<10} {f'{rp:.2f}' if rp else 'N/A':<8} {f'{now:.2f}' if now else 'N/A':<8} {str(q['trade_date'] if q else 'N/A'):<12} {cs:<10} {st}", flush=True)
            
            print("-" * 80, flush=True)
            n = len(stocks)
            print(f"\nTotal: {n} | Up: {g} | Down: {l} | Avg: {tc/n:+.2f}%", flush=True)
    
    print("\nDone.", flush=True)
except Exception as e:
    import traceback
    print(f"ERROR: {e}", flush=True)
    print(traceback.format_exc(), flush=True)
