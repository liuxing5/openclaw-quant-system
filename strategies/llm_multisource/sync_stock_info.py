"""每日同步全 A 股代码-名称表，用于股票名称匹配"""
import os
import sys
import time
from datetime import date
from psycopg2.extras import execute_values
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

sys.path.insert(0, os.path.join(BASE_DIR, '../..'))
from core.db.connection import get_db_fresh


def sync():
    import akshare as ak
    import pandas as pd

    logger.info("同步 A 股代码-名称表...")
    df = None
    for attempt in range(2):
        try:
            df = ak.stock_info_a_code_name()
            break
        except Exception as e:
            if attempt < 1:
                logger.warning(f"AKShare stock_info_a_code_name 失败, 等待 5s: {e}")
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
        elif code.startswith(('8', '4')):
            ts_code = code + '.BJ'
            market = 'BJ'
        else:
            continue
        name = str(r['name']).strip()
        is_st = 'ST' in name.upper() or '*ST' in name.upper()
        rows.append((ts_code, name, market, None, is_st, True))

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        execute_values(cur, """
            INSERT INTO stock_basic_info (ts_code, stock_name, market, list_date, is_st, is_active)
            VALUES %s
            ON CONFLICT (ts_code) DO UPDATE SET
                stock_name=EXCLUDED.stock_name,
                is_st=EXCLUDED.is_st,
                updated_at=NOW();
        """, rows)
        conn.commit()
        logger.info(f"同步 {len(rows)} 只股票基本信息")

        # 上市日期回填已移至独立脚本，不在每日同步中执行
        # 避免逐只股票请求导致超时（100只 × 0.3s = 30s+）

        cur.close()
        logger.info("股票信息同步完成")
    except Exception as e:
        logger.error(f"sync 失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()


if __name__ == '__main__':
    sync()
