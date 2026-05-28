"""查询数据库推荐股票记录，对比从推荐到现在的涨跌幅"""
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

DB_CONFIG = dict(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=6543,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require',
    connect_timeout=10,
)

def get_db():
    import time
    for attempt in range(5):
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return conn
        except Exception as e:
            err = str(e)
            if 'max clients' in err:
                wait = 10 * (attempt + 1)
                print(f"Pool full, wait {wait}s ({attempt+1}/5)...", flush=True)
                time.sleep(wait)
            elif 'SSL' in err or 'unexpected' in err:
                print(f"SSL error, retry ({attempt+1}/5)...", flush=True)
                time.sleep(3)
            else:
                print(f"Error: {err}", flush=True)
                time.sleep(3)
    raise Exception("All retries failed")

conn = get_db()
cur = conn.cursor(cursor_factory=RealDictCursor)

# 获取最近有推荐股票的日期
cur.execute("""
    SELECT snapshot_date, source, run_mode, COUNT(*) as cnt
    FROM daily_candidates
    WHERE selected = true
    GROUP BY snapshot_date, source, run_mode
    ORDER BY snapshot_date DESC
    LIMIT 10
""")
recent = cur.fetchall()

print("=" * 80, flush=True)
print("最近推荐记录:", flush=True)
print("=" * 80, flush=True)
for r in recent:
    print(f"  {r['snapshot_date']} | {r['source']} | {r['run_mode']} | {r['cnt']}只", flush=True)

if not recent:
    print("No recommendations found", flush=True)
else:
    latest_date = recent[0]['snapshot_date']
    latest_source = recent[0]['source']
    latest_mode = recent[0]['run_mode']
    
    print(f"\n查询最近一次: {latest_date} ({latest_source}, {latest_mode})", flush=True)
    print("=" * 80, flush=True)
    
    cur.execute("""
        SELECT ts_code, stock_name, final_score, entry_low, entry_high, stop_loss, target_1, target_2
        FROM daily_candidates
        WHERE snapshot_date = %s AND source = %s AND run_mode = %s AND selected = true
        ORDER BY final_score DESC NULLS LAST
    """, (latest_date, latest_source, latest_mode))
    
    stocks = cur.fetchall()
    print(f"共 {len(stocks)} 只推荐股票", flush=True)
    
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
        
        print(f"\n{'代码':<12} {'名称':<10} {'推荐价':<10} {'最新价':<10} {'日期':<12} {'涨跌幅':<10} 状态", flush=True)
        print("-" * 80, flush=True)
        
        gains = 0
        losses = 0
        total_chg = 0
        
        for s in stocks:
            code = s['ts_code']
            name = (s['stock_name'] or '')[:10]
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
                if chg > 0:
                    status = "UP"
                    gains += 1
                elif chg < 0:
                    status = "DOWN"
                    losses += 1
                else:
                    status = "FLAT"
                total_chg += chg
            else:
                now = None
                dt = q['trade_date'] if q else None
                chg_str = "N/A"
                status = "NO_DATA"
            
            rp = f"{rec_price:.3f}" if rec_price else "N/A"
            np_str = f"{now:.3f}" if now else "N/A"
            print(f"{code:<12} {name:<10} {rp:<10} {np_str:<10} {str(dt):<12} {chg_str:<10} {status}", flush=True)
        
        print("-" * 80, flush=True)
        n = len(stocks)
        avg = total_chg / n if n else 0
        print(f"\n统计: 共{n}只 | 涨{gains}只 | 跌{losses}只 | 平均涨跌幅: {avg:+.2f}%", flush=True)

cur.close()
conn.close()
print("\nDone.", flush=True)
