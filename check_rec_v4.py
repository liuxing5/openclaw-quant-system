"""查询数据库推荐股票记录，对比从推荐到现在的涨跌幅"""
import psycopg2
from psycopg2.extras import RealDictCursor
import time

DB_CONFIG = dict(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=6543,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
    connect_timeout=10,
)

out = []
def p(msg):
    out.append(str(msg))

def get_conn():
    for i in range(3):
        try:
            c = psycopg2.connect(**DB_CONFIG)
            cur = c.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return c
        except Exception as e:
            p(f"Connect retry {i+1}: {e}")
            time.sleep(3)
    raise Exception("Connect failed")

def query_with_retry(sql, params=None, max_retries=3):
    for i in range(max_retries):
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return rows
        except Exception as e:
            p(f"Query retry {i+1}: {e}")
            time.sleep(2)
    raise Exception("Query failed")

try:
    # Step 1: Get recent recommendation dates
    recent = query_with_retry("""
        SELECT snapshot_date, source, run_mode, COUNT(*) as cnt
        FROM daily_candidates
        WHERE selected = true
        GROUP BY snapshot_date, source, run_mode
        ORDER BY snapshot_date DESC
        LIMIT 10
    """)
    
    p("=" * 80)
    p("Recent recommendations:")
    p("=" * 80)
    for r in recent:
        p(f"  {r['snapshot_date']} | {r['source']} | {r['run_mode']} | {r['cnt']}")
    
    if not recent:
        p("No recommendations found")
    else:
        ld = recent[0]['snapshot_date']
        ls = recent[0]['source']
        lm = recent[0]['run_mode']
        p(f"\nLatest: {ld} ({ls}, {lm})")
        p("=" * 80)
        
        # Step 2: Get recommended stocks
        stocks = query_with_retry("""
            SELECT ts_code, stock_name, entry_low, entry_high
            FROM daily_candidates
            WHERE snapshot_date = %s AND source = %s AND run_mode = %s AND selected = true
            ORDER BY final_score DESC NULLS LAST
        """, (ld, ls, lm))
        
        p(f"Found {len(stocks)} stocks")
        
        if stocks:
            codes = [s['ts_code'] for s in stocks]
            
            # Step 3: Get current quotes (batch in small groups to avoid timeout)
            all_quotes = {}
            batch_size = 10
            for i in range(0, len(codes), batch_size):
                batch = codes[i:i+batch_size]
                ph = ','.join(['%s'] * len(batch))
                rows = query_with_retry(f"""
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
            
            # Step 4: Print results
            p(f"\n{'Code':<12} {'Name':<10} {'Rec':<8} {'Now':<8} {'Date':<12} {'Chg%':<10} Status")
            p("-" * 80)
            
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
                    now = None
                    cs = "N/A"
                    st = "NO_DATA"
                
                p(f"{code:<12} {name:<10} {f'{rp:.2f}' if rp else 'N/A':<8} {f'{now:.2f}' if now else 'N/A':<8} {str(q['trade_date'] if q else 'N/A'):<12} {cs:<10} {st}")
            
            p("-" * 80)
            n = len(stocks)
            p(f"\nTotal: {n} | Up: {g} | Down: {l} | Avg: {tc/n:+.2f}%")
    
    p("\nDone")
except Exception as e:
    import traceback
    p(f"ERROR: {e}")
    p(traceback.format_exc())

with open('rec_result.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
