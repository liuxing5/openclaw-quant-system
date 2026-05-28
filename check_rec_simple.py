"""查询数据库推荐股票记录，对比从推荐到现在的涨跌幅 - 简化版"""
import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = {
    'host': 'aws-1-ap-northeast-1.pooler.supabase.com',
    'port': 6543,
    'user': 'postgres.qoakbxswwjqfsgbcgepr',
    'password': 'wYFBB91zViSrk2vl',
    'dbname': 'postgres',
    'sslmode': 'require',
    'connect_timeout': 30,
}

def get_db():
    import time
    last_err = None
    for attempt in range(10):
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return conn
        except Exception as e:
            last_err = e
            err_str = str(e)
            if 'max clients reached' in err_str:
                wait = 10 * (attempt + 1)
                print(f"连接池已满，等待 {wait}s 后重试 ({attempt+1}/10)...", flush=True)
                time.sleep(wait)
            elif 'timed out' in err_str or 'timeout' in err_str.lower():
                wait = 5
                print(f"连接超时，等待 {wait}s 后重试 ({attempt+1}/10)...", flush=True)
                time.sleep(wait)
            else:
                print(f"连接错误: {e}，等待 3s 重试 ({attempt+1}/10)...", flush=True)
                time.sleep(3)
    raise last_err

lines = []
def log(msg):
    lines.append(msg)
    print(msg, flush=True)

log(f"DB_CONFIG: {DB_CONFIG}")
log(f"Starting at: {__import__('datetime').datetime.now()}")

try:
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
    recent_dates = cur.fetchall()
    
    log("=" * 80)
    log("最近推荐记录:")
    log("=" * 80)
    for row in recent_dates:
        log(f"  {row['snapshot_date']} | source={row['source']} | run_mode={row['run_mode']} | {row['cnt']}只")
    
    if not recent_dates:
        log("没有找到推荐记录")
    else:
        latest_date = recent_dates[0]['snapshot_date']
        latest_source = recent_dates[0]['source']
        latest_run_mode = recent_dates[0]['run_mode']
        
        log(f"\n查询最近一次推荐: {latest_date} ({latest_source}, {latest_run_mode})")
        log("=" * 80)
        
        cur.execute("""
            SELECT id, ts_code, stock_name, final_score, entry_low, entry_high, stop_loss, target_1, target_2
            FROM daily_candidates
            WHERE snapshot_date = %s AND source = %s AND run_mode = %s AND selected = true
            ORDER BY final_score DESC NULLS LAST
        """, (latest_date, latest_source, latest_run_mode))
        
        stocks = cur.fetchall()
        
        if stocks:
            ts_codes = [s['ts_code'] for s in stocks]
            placeholders = ','.join(['%s'] * len(ts_codes))
            cur.execute(f"""
                SELECT dq.ts_code, dq.trade_date, dq.close, dq.open, dq.high, dq.low
                FROM daily_quotes dq
                INNER JOIN (
                    SELECT ts_code, MAX(trade_date) as max_date
                    FROM daily_quotes
                    WHERE ts_code IN ({placeholders})
                    GROUP BY ts_code
                ) latest ON dq.ts_code = latest.ts_code AND dq.trade_date = latest.max_date
                ORDER BY dq.ts_code
            """, ts_codes)
            
            quotes = {row['ts_code']: row for row in cur.fetchall()}
            
            log(f"\n{'代码':<12} {'名称':<10} {'推荐价':<10} {'最新价':<10} {'最新日期':<12} {'涨跌幅':<10} {'状态'}")
            log("-" * 90)
            
            total_gain = 0
            gain_count = 0
            loss_count = 0
            
            for stock in stocks:
                ts_code = stock['ts_code']
                name = stock['stock_name'] or ts_code
                entry_low = stock['entry_low']
                entry_high = stock['entry_high']
                
                if entry_low and entry_high:
                    rec_price = float((entry_low + entry_high) / 2)
                elif entry_low:
                    rec_price = float(entry_low)
                elif entry_high:
                    rec_price = float(entry_high)
                else:
                    rec_price = None
                
                quote = quotes.get(ts_code)
                
                if quote and rec_price and rec_price > 0:
                    current_price = float(quote['close'])
                    trade_date = quote['trade_date']
                    change_pct = (current_price - rec_price) / rec_price * 100
                    change_str = f"{change_pct:+.2f}%"
                    
                    if change_pct > 0:
                        status = "UP"
                        gain_count += 1
                    elif change_pct < 0:
                        status = "DOWN"
                        loss_count += 1
                    else:
                        status = "FLAT"
                    
                    total_gain += change_pct
                else:
                    current_price = None
                    trade_date = quote['trade_date'] if quote else None
                    change_str = "N/A"
                    status = "NO_DATA"
                
                rec_price_str = f"{rec_price:.3f}" if rec_price else "N/A"
                current_price_str = f"{current_price:.3f}" if current_price else "N/A"
                trade_date_str = str(trade_date) if trade_date else "N/A"
                
                log(f"{ts_code:<12} {name:<10} {rec_price_str:<10} {current_price_str:<10} {trade_date_str:<12} {change_str:<10} {status}")
            
            log("-" * 90)
            total = len(stocks)
            avg_gain = total_gain / total if total > 0 else 0
            log(f"\n统计: 共{total}只 | 涨{gain_count}只 | 跌{loss_count}只 | 平均涨跌幅: {avg_gain:+.2f}%")
        else:
            log("该日期没有选中的股票")
    
    cur.close()
    conn.close()
    
except Exception as e:
    import traceback
    log(f"ERROR: {e}")
    log(traceback.format_exc())

# Write to file
with open('recommend_result.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
