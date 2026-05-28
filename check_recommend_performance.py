"""查询数据库推荐股票记录，对比从推荐到现在的涨跌幅"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date

DB_CONFIG = {
    'host': 'aws-1-ap-northeast-1.pooler.supabase.com',
    'port': 5432,
    'user': 'postgres.qoakbxswwjqfsgbcgepr',
    'password': 'wYFBB91zViSrk2vl',
    'dbname': 'postgres',
    'sslmode': 'require',
    'connect_timeout': 30,
}

# Try direct connection if pooler is full
DB_CONFIG_DIRECT = {
    'host': 'db.qoakbxswwjqfsgbcgepr.supabase.co',
    'port': 5432,
    'user': 'postgres.qoakbxswwjqfsgbcgepr',
    'password': 'wYFBB91zViSrk2vl',
    'dbname': 'postgres',
    'sslmode': 'require',
    'connect_timeout': 30,
}


def get_db():
    """Try pooler first, fall back to direct, with retry."""
    import time
    last_err = None
    for attempt in range(3):
        for cfg in [DB_CONFIG, DB_CONFIG_DIRECT]:
            try:
                return psycopg2.connect(**cfg)
            except Exception as e:
                last_err = e
                if 'max clients reached' in str(e):
                    time.sleep(2)
                continue
    raise last_err


def get_latest_recommendations():
    """获取最近一次推荐的股票列表（selected=True的记录）"""
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
    
    print("=" * 80)
    print("最近推荐记录:")
    print("=" * 80)
    for row in recent_dates:
        print(f"  {row['snapshot_date']} | source={row['source']} | run_mode={row['run_mode']} | {row['cnt']}只")
    
    # 取最近一次推荐
    if not recent_dates:
        print("没有找到推荐记录")
        return []
    
    latest_date = recent_dates[0]['snapshot_date']
    latest_source = recent_dates[0]['source']
    latest_run_mode = recent_dates[0]['run_mode']
    
    print(f"\n查询最近一次推荐: {latest_date} ({latest_source}, {latest_run_mode})")
    print("=" * 80)
    
    # 获取该次推荐的所有股票
    cur.execute("""
        SELECT id, ts_code, stock_name, final_score, entry_low, entry_high, stop_loss, target_1, target_2
        FROM daily_candidates
        WHERE snapshot_date = %s AND source = %s AND run_mode = %s AND selected = true
        ORDER BY final_score DESC NULLS LAST
    """, (latest_date, latest_source, latest_run_mode))
    
    stocks = cur.fetchall()
    cur.close()
    
    return stocks, latest_date


def get_current_quotes(ts_codes):
    """获取股票最新行情"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # 获取每只股票的最新行情
    placeholders = ','.join(['%s'] * len(ts_codes))
    cur.execute(f"""
        SELECT dq.ts_code, dq.trade_date, dq.close, dq.open, dq.high, dq.low, 
               dq.pct_chg, dq.volume, dq.amount
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
    cur.close()
    
    return quotes


def main():
    result = get_latest_recommendations()
    if not result:
        return
    
    stocks, rec_date = result
    
    if not stocks:
        print("该日期没有选中的股票")
        return
    
    ts_codes = [s['ts_code'] for s in stocks]
    quotes = get_current_quotes(ts_codes)
    
    print(f"\n{'代码':<12} {'名称':<10} {'推荐日':<12} {'推荐价':<10} {'最新价':<10} {'最新日期':<12} {'涨跌幅':<10} {'状态'}")
    print("-" * 100)
    
    total_gain = 0
    gain_count = 0
    loss_count = 0
    
    for stock in stocks:
        ts_code = stock['ts_code']
        name = stock['stock_name'] or ts_code
        entry_low = stock['entry_low']
        entry_high = stock['entry_high']
        
        # 推荐价取区间中值
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
                status = "📈 涨"
                gain_count += 1
            elif change_pct < 0:
                status = "📉 跌"
                loss_count += 1
            else:
                status = "➡️ 平"
            
            total_gain += change_pct
        else:
            current_price = quote['close'] if quote else None
            trade_date = quote['trade_date'] if quote else None
            change_str = "N/A"
            status = "❓ 无数据"
        
        rec_price_str = f"{rec_price:.3f}" if rec_price else "N/A"
        current_price_str = f"{current_price:.3f}" if current_price else "N/A"
        trade_date_str = str(trade_date) if trade_date else "N/A"
        
        print(f"{ts_code:<12} {name:<10} {rec_date:<12} {rec_price_str:<10} {current_price_str:<10} {trade_date_str:<12} {change_str:<10} {status}")
    
    print("-" * 100)
    total = len(stocks)
    avg_gain = total_gain / total if total > 0 else 0
    print(f"\n统计: 共{total}只 | 涨{gain_count}只 | 跌{loss_count}只 | 平均涨跌幅: {avg_gain:+.2f}%")


if __name__ == '__main__':
    import io
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        main()
    except Exception as e:
        import traceback
        print(f"ERROR: {e}")
        traceback.print_exc()
    output = sys.stdout.getvalue()
    sys.stdout = old_stdout
    
    # Write to file
    with open('recommend_result.txt', 'w', encoding='utf-8') as f:
        f.write(output)
    
    # Also print
    print(output)
