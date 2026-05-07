"""每日同步全 A 股代码-名称表，用于股票名称匹配"""
import os
import time
from datetime import date
import psycopg2
from psycopg2.extras import execute_values
from loguru import logger
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
    )


def sync():
    import akshare as ak
    import pandas as pd

    logger.info("同步 A 股代码-名称表...")
    df = None
    for attempt in range(3):
        try:
            df = ak.stock_info_a_code_name()
            break
        except Exception as e:
            if attempt < 2:
                logger.warning(f"AKShare stock_info_a_code_name 失败 (尝试 {attempt+1}/3): {e}")
                time.sleep(5)
            else:
                logger.error(f"AKShare stock_info_a_code_name 最终失败: {e}")
                return

    rows = []
    for _, r in df.iterrows():
        code = str(r['code']).zfill(6)
        if code.startswith(('6', '688')):
            ts_code = code + '.SH'
            market = 'SH'
        elif code.startswith(('0', '00', '30', '301')):
            ts_code = code + '.SZ'
            market = 'SZ'
        else:
            continue
        name = str(r['name']).strip()
        is_st = 'ST' in name.upper() or '*ST' in name.upper()
        rows.append((ts_code, name, market, is_st, True))

    conn = get_db()
    cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO stock_basic_info (ts_code, stock_name, market, is_st, is_active)
        VALUES %s
        ON CONFLICT (ts_code) DO UPDATE SET
            stock_name=EXCLUDED.stock_name,
            is_st=EXCLUDED.is_st,
            updated_at=NOW();
    """, rows)
    conn.commit()
    logger.info(f"同步 {len(rows)} 只股票")
    cur.close()
    conn.close()


if __name__ == '__main__':
    sync()
