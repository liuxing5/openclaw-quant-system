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
        sslmode='require',
    )


def sync():
    import akshare as ak
    import pandas as pd

    logger.info("同步 A 股代码-名称表...")
    df = None
    for attempt in range(5):
        try:
            df = ak.stock_info_a_code_name()
            break
        except Exception as e:
            wait = 5 * (2 ** attempt)
            if attempt < 4:
                logger.warning(f"AKShare stock_info_a_code_name 失败 (尝试 {attempt+1}/5), 等待 {wait}s: {e}")
                time.sleep(wait)
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
        rows.append((ts_code, name, market, None, is_st, True))

    conn = get_db()
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

    # 回填 list_date：只处理 list_date 为 NULL 的股票
    cur.execute("""
        SELECT ts_code FROM stock_basic_info WHERE list_date IS NULL LIMIT 100;
    """)
    missing = [row[0] for row in cur.fetchall()]

    if missing:
        logger.info(f"开始回填 {len(missing)} 只股票的上市日期...")
        for ts_code in missing:
            try:
                code = ts_code.split('.')[0]
                info_df = ak.stock_individual_info_em(symbol=code)
                if info_df is not None and not info_df.empty:
                    list_date_row = info_df[info_df['item'] == '上市时间']
                    if not list_date_row.empty:
                        list_date_str = str(list_date_row['value'].iloc[0])
                        if list_date_str and list_date_str != 'nan' and len(list_date_str) == 8:
                            list_date = pd.to_datetime(list_date_str, format='%Y%m%d').date()
                            cur.execute("""
                                UPDATE stock_basic_info SET list_date = %s WHERE ts_code = %s;
                            """, (list_date, ts_code))
                            conn.commit()
                            logger.debug(f"{ts_code} list_date = {list_date}")
                time.sleep(0.3)
            except Exception as e:
                logger.debug(f"获取 {ts_code} 上市日期失败: {e}")
                continue

    cur.close()
    conn.close()
    logger.info("股票信息同步完成")


if __name__ == '__main__':
    sync()
