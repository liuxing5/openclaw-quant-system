#!/usr/bin/env python3
"""批量填充 stock_basic_info.list_date - 使用多种数据源"""
import os
import sys
import time
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.db.connection import get_db_fresh


def fetch_list_date_akshare(code):
    try:
        import akshare as ak
        df = ak.stock_individual_info_em(symbol=code)
        if df is not None and not df.empty:
            row = df[df['item'] == '上市时间']
            if not row.empty:
                date_str = str(row['value'].iloc[0])
                if date_str and date_str != 'nan' and len(date_str) >= 8:
                    return pd.to_datetime(date_str[:8], format='%Y%m%d').date()
    except Exception as e:
        print(f"  AKShare失败: {str(e)[:50]}")
    return None

def fetch_list_date_tushare(code):
    try:
        import tushare as ts
        ts.set_token('your_tushare_token')
        pro = ts.pro_api()
        df = pro.stock_basic(ts_code=code)
        if df is not None and not df.empty:
            list_date_str = str(df['list_date'].iloc[0])
            if list_date_str and list_date_str != 'nan':
                return pd.to_datetime(list_date_str, format='%Y%m%d').date()
    except Exception as e:
        print(f"  Tushare失败: {str(e)[:50]}")
    return None

def fetch_list_date_web(code):
    try:
        import requests
        from bs4 import BeautifulSoup
        import re

        url = f'https://finance.sina.com.cn/stock/{code}.shtml'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')

        for tag in soup.find_all(['span', 'div', 'td']):
            text = tag.get_text()
            if '上市日期' in text or '上市时间' in text:
                match = re.search(r'\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2}|\d{8}', text)
                if match:
                    date_str = match.group()
                    date_str = date_str.replace('-', '').replace('/', '')
                    if len(date_str) == 8:
                        return pd.to_datetime(date_str, format='%Y%m%d').date()
    except Exception as e:
        print(f"  Web爬虫失败: {str(e)[:50]}")
    return None

def batch_fill_list_date(limit=100):
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()

        cur.execute('''
            SELECT ts_code FROM stock_basic_info WHERE list_date IS NULL LIMIT %s;
        ''', (limit,))
        missing = [row[0] for row in cur.fetchall()]

        if not missing:
            print("✅ 没有需要填充的股票")
            return 0

        print(f"开始填充 {len(missing)} 只股票的上市日期...")
        success = 0
        failed = []

        for i, ts_code in enumerate(missing, 1):
            code = ts_code.split('.')[0]
            print(f"\n[{i}/{len(missing)}] {ts_code}")

            list_date = None

            list_date = fetch_list_date_akshare(code)
            if list_date:
                print(f"  ✅ AKShare成功: {list_date}")

            if not list_date:
                list_date = fetch_list_date_tushare(ts_code)
                if list_date:
                    print(f"  ✅ Tushare成功: {list_date}")

            if not list_date:
                list_date = fetch_list_date_web(code)
                if list_date:
                    print(f"  ✅ Web爬虫成功: {list_date}")

            if list_date:
                cur.execute('''
                    UPDATE stock_basic_info SET list_date = %s WHERE ts_code = %s;
                ''', (list_date, ts_code))
                conn.commit()
                success += 1
            else:
                failed.append(ts_code)
                print(f"  ❌ 所有方法均失败")

            time.sleep(0.5)

        cur.close()

        print(f"\n📊 完成: 成功 {success}/{len(missing)}")
        if failed:
            print(f"❌ 失败列表: {failed}")

        return success
    except Exception as e:
        print(f"batch_fill_list_date 失败: {e}")
        return 0
    finally:
        if conn and not conn.closed:
            conn.close()

if __name__ == '__main__':
    success_count = batch_fill_list_date(limit=50)
    print(f"\n✅ 本次填充 {success_count} 条上市日期")
