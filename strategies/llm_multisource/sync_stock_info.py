"""每日同步全 A 股代码-名称表，用于股票名称匹配"""
import os
import sys
import time
from datetime import date, datetime
from psycopg2.extras import execute_values
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 优先仓库根 .env，回退到本目录 .env（CI 中由 workflow 直接注入环境变量）。
# load_dotenv 默认不覆盖已有变量，顺序加载安全。
load_dotenv(os.path.abspath(os.path.join(BASE_DIR, '..', '..', '.env')))
load_dotenv(os.path.join(BASE_DIR, '.env'))

sys.path.insert(0, os.path.join(BASE_DIR, '../..'))
from core.db.connection import get_db_fresh
from core.utils.ts_code import baostock_to_standard


def _fetch_delisted_ts_codes():
    """用 baostock query_stock_basic 获取已退市股票 ts_code 集合（幸存者偏差标记）。

    baostock 字段：type（1=股票）、status（1=上市, 0=退市）、outDate（退市日期, 在市为空）。
    返回 set(ts_code)。任一环节失败仅告警并返回空集，不阻断主同步流程。
    """
    try:
        import baostock as bs
    except Exception as e:
        logger.warning(f"baostock 不可用，跳过退市同步: {e}")
        return set()

    today = date.today()
    delisted = set()
    lg = bs.login()
    if lg.error_code != '0':
        logger.warning(f"baostock 登录失败，跳过退市同步: {lg.error_msg}")
        return set()
    try:
        rs = bs.query_stock_basic()
        if rs.error_code != '0':
            logger.warning(f"query_stock_basic 失败: {rs.error_msg}")
            return set()
        while rs.next():
            row = rs.get_row_data()
            # 顺序: code, code_name, ipoDate, outDate, type, status
            if len(row) < 6:
                continue
            code, out_date, typ, status = row[0], row[3], row[4], row[5]
            if typ != '1':  # 仅股票
                continue
            is_delisted = (status == '0')
            if not is_delisted and out_date:
                try:
                    is_delisted = datetime.strptime(out_date, '%Y-%m-%d').date() <= today
                except (ValueError, TypeError):
                    is_delisted = False
            if is_delisted:
                delisted.add(baostock_to_standard(code))
        logger.info(f"baostock 退市股票: {len(delisted)} 只")
    except Exception as e:
        logger.warning(f"退市同步异常: {e}")
    finally:
        bs.logout()
    return delisted


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

    # 退市集合（幸存者偏差标记）：akshare 当日列表通常不含退市股，
    # 需借 baostock 判定后单独 UPDATE 历史行。
    delisted = _fetch_delisted_ts_codes()

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
        is_active = ts_code not in delisted
        rows.append((ts_code, name, market, None, is_st, is_active))

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
                is_active=EXCLUDED.is_active,
                updated_at=NOW();
        """, rows)
        conn.commit()
        logger.info(f"同步 {len(rows)} 只股票基本信息")

        # 退市股通常不在 akshare 当日列表中 → 显式 UPDATE 历史行 is_active=False
        if delisted:
            cur.execute(
                "UPDATE stock_basic_info SET is_active = FALSE, updated_at = NOW() "
                "WHERE ts_code = ANY(%s)",
                (list(delisted),),
            )
            logger.info(f"标记退市 is_active=False: {cur.rowcount} 只")
            conn.commit()

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
