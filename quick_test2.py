import psycopg2
from psycopg2.extras import RealDictCursor
import time

print("Trying session mode pooler (5432)...", flush=True)

for attempt in range(15):
    try:
        conn = psycopg2.connect(
            host='aws-1-ap-northeast-1.pooler.supabase.com',
            port=5432,
            user='postgres.qoakbxswwjqfsgbcgepr',
            password='wYFBB91zViSrk2vl',
            dbname='postgres',
            sslmode='require',
            connect_timeout=15,
        )
        conn.autocommit = True
        print(f"Connected on attempt {attempt+1}!", flush=True)
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT count(*) FROM daily_candidates WHERE selected = true")
        cnt = cur.fetchone()[0]
        print(f"Selected candidates: {cnt}", flush=True)
        
        cur.execute("""
            SELECT snapshot_date, source, run_mode, COUNT(*) as cnt
            FROM daily_candidates WHERE selected = true
            GROUP BY snapshot_date, source, run_mode
            ORDER BY snapshot_date DESC LIMIT 5
        """)
        rows = cur.fetchall()
        print(f"Got {len(rows)} date groups", flush=True)
        for r in rows:
            print(f"  {r['snapshot_date']} | {r['source']} | {r['run_mode']} | {r['cnt']}", flush=True)
        
        if rows:
            ld, ls, lm = rows[0]['snapshot_date'], rows[0]['source'], rows[0]['run_mode']
            print(f"\nLatest: {ld} ({ls}, {lm})", flush=True)
            
            cur.execute("""
                SELECT ts_code, stock_name, entry_low, entry_high
                FROM daily_candidates
                WHERE snapshot_date = %s AND source = %s AND run_mode = %s AND selected = true
                ORDER BY final_score DESC NULLS LAST LIMIT 20
            """, (ld, ls, lm))
            stocks = cur.fetchall()
            print(f"Top {len(stocks)} stocks:", flush=True)
            
            codes = [s['ts_code'] for s in stocks]
            ph = ','.join(['%s'] * len(codes))
            cur.execute(f"""
                SELECT ts_code, trade_date, close FROM daily_quotes
                WHERE ts_code IN ({ph})
                AND trade_date = (SELECT MAX(trade_date) FROM daily_quotes WHERE ts_code = daily_quotes.ts_code)
            """, codes)
            quotes = {r['ts_code']: r for r in cur.fetchall()}
            
            print(f"\n{'Code':<12} {'Name':<8} {'Rec':<8} {'Now':<8} {'Date':<12} {'Chg%':<10} Status", flush=True)
            print("-" * 75, flush=True)
            
            g = l = tc = 0
            for s in stocks:
                code = s['ts_code']
                name = (s.get('stock_name') or '')[:8]
                el, eh = s.get('entry_low'), s.get('entry_high')
                rp = float((el + eh) / 2) if el and eh else (float(el) if el else (float(eh) if eh else None))
                q = quotes.get(code)
                if q and rp and rp > 0:
                    now = float(q['close'])
                    chg = (now - rp) / rp * 100
                    cs = f"{chg:+.2f}%"
                    st = "UP" if chg > 0 else ("DOWN" if chg < 0 else "FLAT")
                    if chg > 0: g += 1
                    elif chg < 0: l += 1
                    tc += chg
                else:
                    now = None; cs = "N/A"; st = "NO"
                print(f"{code:<12} {name:<8} {f'{rp:.2f}' if rp else 'N/A':<8} {f'{now:.2f}' if now else 'N/A':<8} {str(q['trade_date'] if q else ''):<12} {cs:<10} {st}", flush=True)
            
            n = len(stocks)
            print(f"\nTotal: {n} | Up: {g} | Down: {l} | Avg: {tc/n:+.2f}%", flush=True)
        
        cur.close()
        conn.close()
        print("\nDone!", flush=True)
        break
    except Exception as e:
        err = str(e)
        if 'max clients' in err:
            w = 15 * (attempt + 1)
            print(f"Pool full, wait {w}s ({attempt+1}/15)...", flush=True)
            time.sleep(w)
        else:
            print(f"Error: {err[:120]}", flush=True)
            time.sleep(5)
else:
    print("All retries failed", flush=True)
