"""结构化数据直接转推荐（不走 LLM，省钱省时）
龙虎榜/涨停板/机构调研 -> extracted_recommendations
"""
import os
from datetime import date, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
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


def lhb_to_extraction():
    """龙虎榜 -> extracted_recommendations
    净买入 > 0 -> strength=3, type=watch
    机构席位 -> strength=4, type=buy
    """
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    today = date.today()
    cutoff = today - timedelta(days=2)

    cur.execute("""
        SELECT r.id AS raw_id, r.source_name, r.pub_time, r.title, r.content
        FROM raw_signals r
        WHERE r.source_name = 'AKShare-龙虎榜'
          AND r.fetch_time >= %s
          AND NOT EXISTS (
              SELECT 1 FROM extracted_recommendations e WHERE e.raw_signal_id = r.id
          );
    """, (cutoff,))
    rows = cur.fetchall()

    stored = 0
    for r in rows:
        title = r['title'] or ''
        content = r['content'] or ''
        net_match = None
        try:
            import re
            m = re.search(r'净买入([-\d.]+)亿', title)
            if m:
                net_match = float(m.group(1)) * 1e8
        except Exception:
            pass

        is_inst = '机构' in title or '机构' in content
        strength = 4 if (is_inst and net_match and net_match > 0) else (3 if (net_match and net_match > 0) else 2)
        rec_type = 'buy' if strength >= 4 else 'watch'

        ts_code = None
        stock_name = ''
        try:
            m = re.search(r'([0-9]{6})', title)
            if m:
                code = m.group(1)
                ts_code = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
            m = re.search(r'龙虎榜:\s*(\S+)', title)
            if m:
                stock_name = m.group(1)
        except Exception:
            pass

        if not ts_code:
            continue

        cur.execute("""
            INSERT INTO extracted_recommendations
            (raw_signal_id, source_name, ts_code, stock_name, recommendation_type,
             strength, logic_category, logic_summary, confidence, pub_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """, (
            r['raw_id'], r['source_name'], ts_code, stock_name,
            rec_type, strength,
            '龙虎榜' if not is_inst else '机构买入',
            f"净买入{net_match/1e8:.2f}亿" if net_match else '',
            0.7 if is_inst else 0.5,
            r['pub_time'],
        ))
        stored += 1

    conn.commit()
    cur.close(); conn.close()
    logger.info(f"龙虎榜 fast path: {stored} 条")


def zt_pool_to_extraction():
    """涨停板 -> extracted_recommendations
    首板 -> strength=2, type=watch
    2-3 板 -> strength=3, type=watch
    4 板以上 -> strength=4, type=buy
    """
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    today = date.today()
    cutoff = today - timedelta(days=2)

    cur.execute("""
        SELECT r.id AS raw_id, r.source_name, r.pub_time, r.title, r.content
        FROM raw_signals r
        WHERE r.source_name = 'AKShare-涨停板'
          AND r.fetch_time >= %s
          AND NOT EXISTS (
              SELECT 1 FROM extracted_recommendations e WHERE e.raw_signal_id = r.id
          );
    """, (cutoff,))
    rows = cur.fetchall()

    stored = 0
    for r in rows:
        title = r['title'] or ''
        import re
        ts_code = None
        stock_name = ''
        lianban = 1
        try:
            m = re.search(r'([0-9]{6})', title)
            if m:
                code = m.group(1)
                ts_code = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
            m = re.search(r'涨停:\s*(\S+)', title)
            if m:
                stock_name = m.group(1)
            m = re.search(r'连板(\d+)', title)
            if m:
                lianban = int(m.group(1))
        except Exception:
            pass

        if not ts_code:
            continue

        strength = 4 if lianban >= 4 else (3 if lianban >= 2 else 2)
        rec_type = 'buy' if lianban >= 4 else 'watch'

        cur.execute("""
            INSERT INTO extracted_recommendations
            (raw_signal_id, source_name, ts_code, stock_name, recommendation_type,
             strength, logic_category, logic_summary, confidence, pub_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """, (
            r['raw_id'], r['source_name'], ts_code, stock_name,
            rec_type, strength,
            '连板' if lianban >= 2 else '首板',
            f'{lianban}连板',
            0.6 if lianban >= 3 else 0.4,
            r['pub_time'],
        ))
        stored += 1

    conn.commit()
    cur.close(); conn.close()
    logger.info(f"涨停板 fast path: {stored} 条")


def jgdy_to_extraction():
    """机构调研 -> extracted_recommendations
    接待机构数 >= 10 -> strength=3, type=watch
    接待机构数 >= 30 -> strength=4, type=buy
    """
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    today = date.today()
    cutoff = today - timedelta(days=7)

    cur.execute("""
        SELECT r.id AS raw_id, r.source_name, r.pub_time, r.title, r.content
        FROM raw_signals r
        WHERE r.source_name = 'AKShare-机构调研'
          AND r.fetch_time >= %s
          AND NOT EXISTS (
              SELECT 1 FROM extracted_recommendations e WHERE e.raw_signal_id = r.id
          );
    """, (cutoff,))
    rows = cur.fetchall()

    stored = 0
    for r in rows:
        title = r['title'] or ''
        content = r['content'] or ''
        import re
        ts_code = None
        stock_name = ''
        count = 0
        try:
            m = re.search(r'([0-9]{6})', title)
            if m:
                code = m.group(1)
                ts_code = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
            m = re.search(r'机构调研:\s*(\S+)', title)
            if m:
                stock_name = m.group(1)
            m = re.search(r'(\d+)家', title)
            if m:
                count = int(m.group(1))
        except Exception:
            pass

        if not ts_code:
            continue

        strength = 4 if count >= 30 else (3 if count >= 10 else 2)
        rec_type = 'buy' if count >= 30 else 'watch'

        cur.execute("""
            INSERT INTO extracted_recommendations
            (raw_signal_id, source_name, ts_code, stock_name, recommendation_type,
             strength, logic_category, logic_summary, confidence, pub_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """, (
            r['raw_id'], r['source_name'], ts_code, stock_name,
            rec_type, strength,
            '机构调研',
            f'{count}家机构调研',
            0.5,
            r['pub_time'],
        ))
        stored += 1

    conn.commit()
    cur.close(); conn.close()
    logger.info(f"机构调研 fast path: {stored} 条")


def main():
    log_file = os.path.join(BASE_DIR, 'logs', 'structured_extraction.log')
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger.add(log_file, rotation='100 MB')

    logger.info("=" * 50)
    logger.info("结构化数据 fast path 提取")
    logger.info("=" * 50)

    lhb_to_extraction()
    zt_pool_to_extraction()
    jgdy_to_extraction()

    logger.info("fast path 完成")


if __name__ == '__main__':
    main()
