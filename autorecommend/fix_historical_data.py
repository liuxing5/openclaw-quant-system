#!/usr/bin/env python3
"""批量修复历史数据问题"""
import os
import time
import psycopg2
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
        sslmode='require',
    )


def fix_raw_signals_source_id(conn):
    """修复 raw_signals.source_id - 通过 source_name 关联 feed_sources"""
    cur = conn.cursor()
    cur.execute("""
        UPDATE raw_signals r
        SET source_id = f.id
        FROM feed_sources f
        WHERE r.source_name = f.name AND r.source_id IS NULL;
    """)
    updated = cur.rowcount
    conn.commit()
    cur.close()
    print(f"✅ 修复 raw_signals.source_id: 更新 {updated} 条记录")


def fix_stock_basic_info_list_date(conn, limit=50):
    """回填 stock_basic_info.list_date - 通过 AKShare 获取上市日期"""
    import akshare as ak
    import pandas as pd
    
    cur = conn.cursor()
    cur.execute("""
        SELECT ts_code FROM stock_basic_info WHERE list_date IS NULL LIMIT %s;
    """, (limit,))
    missing = [row[0] for row in cur.fetchall()]
    
    if not missing:
        print("✅ stock_basic_info.list_date: 无需修复（没有 NULL 值）")
        cur.close()
        return 0
    
    print(f"开始修复 stock_basic_info.list_date: {len(missing)} 条待处理...")
    success = 0
    
    for ts_code in missing:
        try:
            code = ts_code.split('.')[0]
            info_df = ak.stock_individual_info_em(symbol=code)
            if info_df is not None and not info_df.empty:
                list_date_row = info_df[info_df['item'] == '上市时间']
                if not list_date_row.empty:
                    list_date_str = str(list_date_row['value'].iloc[0])
                    if list_date_str and list_date_str != 'nan' and len(list_date_str) >= 8:
                        list_date = pd.to_datetime(list_date_str[:8], format='%Y%m%d').date()
                        cur.execute("""
                            UPDATE stock_basic_info SET list_date = %s WHERE ts_code = %s;
                        """, (list_date, ts_code))
                        conn.commit()
                        success += 1
                        print(f"  {ts_code} -> {list_date}")
            time.sleep(0.3)
        except Exception as e:
            print(f"  {ts_code} 失败: {e}")
            continue
    
    cur.close()
    print(f"✅ 修复 stock_basic_info.list_date: 成功 {success}/{len(missing)}")
    return success


def fix_feed_sources_status(conn):
    """更新 feed_sources.last_success_at - 设置为最近采集时间"""
    cur = conn.cursor()
    
    # 获取每个数据源的最新采集时间
    cur.execute("""
        SELECT source_name, MAX(fetch_time) as last_fetch
        FROM raw_signals
        GROUP BY source_name;
    """)
    source_times = {row[0]: row[1] for row in cur.fetchall()}
    
    updated = 0
    for source_name, last_fetch in source_times.items():
        cur.execute("""
            UPDATE feed_sources
            SET last_success_at = %s, consecutive_failures = 0
            WHERE name = %s AND (last_success_at IS NULL OR last_success_at < %s);
        """, (last_fetch, source_name, last_fetch))
        if cur.rowcount > 0:
            updated += 1
    
    conn.commit()
    cur.close()
    print(f"✅ 修复 feed_sources.status: 更新 {updated} 个数据源")


def show_data_status(conn):
    """显示当前数据状态"""
    cur = conn.cursor()
    
    queries = [
        ('hsgt_individual', 'SELECT COUNT(*), COUNT(CASE WHEN hold_shares = 0 AND hold_market_cap = 0 AND net_buy_amount = 0 THEN 1 END) FROM hsgt_individual'),
        ('lhb_detail', 'SELECT COUNT(*), COUNT(CASE WHEN buy_amt = 0 AND sell_amt = 0 THEN 1 END) FROM lhb_detail'),
        ('stock_basic_info', 'SELECT COUNT(*), COUNT(CASE WHEN list_date IS NULL THEN 1 END) FROM stock_basic_info'),
        ('raw_signals', 'SELECT COUNT(*), COUNT(CASE WHEN source_id IS NULL THEN 1 END) FROM raw_signals'),
        ('feed_sources', 'SELECT COUNT(*), COUNT(CASE WHEN last_success_at IS NULL THEN 1 END) FROM feed_sources'),
    ]
    
    print("\n📊 当前数据状态:")
    print("-" * 60)
    for table, query in queries:
        cur.execute(query)
        total, problem = cur.fetchone()
        status = "✅" if problem == 0 else "⚠️"
        print(f"{status} {table}: 总数={total}, 问题数={problem}")
    
    cur.close()


def main():
    print("🚀 批量修复历史数据脚本")
    print("=" * 60)
    
    try:
        conn = get_db()
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        print("请确保 PostgreSQL 服务已启动")
        return
    
    try:
        show_data_status(conn)
        
        print("\n🔧 开始修复...")
        print("-" * 60)
        
        fix_raw_signals_source_id(conn)
        fix_stock_basic_info_list_date(conn, limit=50)
        fix_feed_sources_status(conn)
        
        print("\n✅ 修复完成！")
        show_data_status(conn)
        
        print("\n📋 剩余事项:")
        print("1. hsgt_individual 需要重新采集历史数据（字段映射修复后）")
        print("2. lhb_detail 需要重新采集历史数据（买卖金额修复后）")
        print("   建议: 运行 python market_data.py 重新获取北向资金和龙虎榜数据")
        
    finally:
        conn.close()


if __name__ == '__main__':
    main()