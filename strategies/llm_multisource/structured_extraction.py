"""结构化数据直接转推荐（不走 LLM，省钱省时）
龙虎榜/涨停板/机构调研/THS强势股/盈利预测/公告 -> extracted_recommendations
"""
import os
import re
import sys
from datetime import date, timedelta, datetime, timezone
from psycopg2.extras import RealDictCursor
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

sys.path.insert(0, os.path.join(BASE_DIR, '..', '..'))
from core.db.connection import get_db_fresh
from core.utils.ts_code import pure_to_ts_code

BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date():
    return datetime.now(BEIJING_TZ).date()


def lhb_to_extraction():
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        today = get_beijing_date()
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
                    ts_code = pure_to_ts_code(code)
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
        cur.close()
        logger.info(f"龙虎榜 fast path: {stored} 条")
    except Exception as e:
        logger.error(f"龙虎榜 fast path 失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()


def zt_pool_to_extraction():
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        today = get_beijing_date()
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
            ts_code = None
            stock_name = ''
            lianban = 1
            try:
                m = re.search(r'([0-9]{6})', title)
                if m:
                    code = m.group(1)
                    ts_code = pure_to_ts_code(code)
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

            if lianban >= 4:
                strength = 3
                rec_type = 'watch'
                confidence = 0.4
            elif lianban >= 2:
                strength = 2
                rec_type = 'watch'
                confidence = 0.35
            else:
                strength = 2
                rec_type = 'watch'
                confidence = 0.3

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
                confidence,
                r['pub_time'],
            ))
            stored += 1

        conn.commit()
        cur.close()
        logger.info(f"涨停板 fast path: {stored} 条")
    except Exception as e:
        logger.error(f"涨停板 fast path 失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()


def jgdy_to_extraction():
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        today = get_beijing_date()
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
            ts_code = None
            stock_name = ''
            count = 0
            try:
                m = re.search(r'([0-9]{6})', title)
                if m:
                    code = m.group(1)
                    ts_code = pure_to_ts_code(code)
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
        cur.close()
        logger.info(f"机构调研 fast path: {stored} 条")
    except Exception as e:
        logger.error(f"机构调研 fast path 失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()


def strong_stocks_to_extraction():
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        today = get_beijing_date()
        cutoff = today - timedelta(days=2)

        cur.execute("""
            SELECT r.id AS raw_id, r.source_name, r.pub_time, r.title, r.content
            FROM raw_signals r
            WHERE r.source_name = 'THS-强势股'
              AND r.fetch_time >= %s
              AND NOT EXISTS (
                  SELECT 1 FROM extracted_recommendations e WHERE e.raw_signal_id = r.id
              );
        """, (cutoff,))
        rows = cur.fetchall()

        stored = 0
        for r in rows:
            title = r['title'] or ''
            ts_code = None
            stock_name = ''
            days = 0
            try:
                m = re.search(r'([0-9]{6})', title)
                if m:
                    code = m.group(1)
                    ts_code = pure_to_ts_code(code)
                m = re.search(r'强势:\s*(\S+)', title)
                if m:
                    stock_name = m.group(1)
                m = re.search(r'(\d+)天', title)
                if m:
                    days = int(m.group(1))
            except Exception:
                pass

            if not ts_code:
                continue

            is_new_high = '创' in title and '新高' in title
            strength = 4 if is_new_high else (3 if days >= 3 else 2)

            cur.execute("""
                INSERT INTO extracted_recommendations
                (raw_signal_id, source_name, ts_code, stock_name, recommendation_type,
                 strength, logic_category, logic_summary, confidence, pub_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                r['raw_id'], r['source_name'], ts_code, stock_name,
                'watch', strength,
                '强势股',
                f'连续上涨{days}天' if days > 0 else title[:100],
                0.4,
                r['pub_time'],
            ))
            stored += 1

        conn.commit()
        cur.close()
        logger.info(f"THS强势股 fast path: {stored} 条")
    except Exception as e:
        logger.error(f"THS强势股 fast path 失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()


def profit_forecast_to_extraction():
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        today = get_beijing_date()
        cutoff = today - timedelta(days=2)

        cur.execute("""
            SELECT r.id AS raw_id, r.source_name, r.pub_time, r.title, r.content
            FROM raw_signals r
            WHERE r.source_name IN ('东财-盈利预测', 'THS-盈利预测')
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
            ts_code = None
            stock_name = ''
            try:
                m = re.search(r'([0-9]{6})', title)
                if m:
                    code = m.group(1)
                    ts_code = pure_to_ts_code(code)
                m = re.search(r'(?:盈利预测|THS预测)[：:]\s*(\S+)', title)
                if m:
                    stock_name = m.group(1)
            except Exception:
                pass

            if not ts_code:
                continue

            buy_count = 0
            m = re.search(r'买入[：:]?\s*(\d+)', content)
            if m:
                buy_count = int(m.group(1))
            report_count = 0
            m = re.search(r'(?:研报数|机构数)[：:]?\s*(\d+)', content)
            if m:
                report_count = int(m.group(1))

            strength = 4 if buy_count >= 20 else (3 if report_count >= 10 else 2)

            cur.execute("""
                INSERT INTO extracted_recommendations
                (raw_signal_id, source_name, ts_code, stock_name, recommendation_type,
                 strength, logic_category, logic_summary, confidence, pub_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                r['raw_id'], r['source_name'], ts_code, stock_name,
                'watch', strength,
                '盈利预测',
                content[:200] if content else title[:100],
                0.5,
                r['pub_time'],
            ))
            stored += 1

        conn.commit()
        cur.close()
        logger.info(f"盈利预测 fast path: {stored} 条")
    except Exception as e:
        logger.error(f"盈利预测 fast path 失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()


def announcements_to_extraction():
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        today = get_beijing_date()
        cutoff = today - timedelta(days=2)

        KEYWORDS_HIGH = ['业绩预增', '业绩大幅增长', '扭亏为盈', '增持', '回购']
        KEYWORDS_MED = ['股权激励', '员工持股', '中标', '签约', '战略合作']

        cur.execute("""
            SELECT r.id AS raw_id, r.source_name, r.pub_time, r.title, r.content
            FROM raw_signals r
            WHERE r.source_name IN ('巨潮-公告', 'MootDX-公告')
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
            ts_code = None
            try:
                m = re.search(r'([0-9]{6})', content or title)
                if m:
                    code = m.group(1)
                    ts_code = pure_to_ts_code(code)
            except Exception:
                pass

            if not ts_code:
                continue

            matched_high = any(kw in title for kw in KEYWORDS_HIGH)
            matched_med = any(kw in title for kw in KEYWORDS_MED)
            if not matched_high and not matched_med:
                continue

            strength = 4 if matched_high else 3
            rec_type = 'buy' if '增持' in title or '回购' in title else 'watch'

            cur.execute("""
                INSERT INTO extracted_recommendations
                (raw_signal_id, source_name, ts_code, stock_name, recommendation_type,
                 strength, logic_category, logic_summary, confidence, pub_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                r['raw_id'], r['source_name'], ts_code, stock_name,
                rec_type, strength,
                '公告事件',
                title[:200],
                0.4,
                r['pub_time'],
            ))
            stored += 1

        conn.commit()
        cur.close()
        logger.info(f"公告 fast path: {stored} 条")
    except Exception as e:
        logger.error(f"公告 fast path 失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()


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
    strong_stocks_to_extraction()
    profit_forecast_to_extraction()
    announcements_to_extraction()

    logger.info("fast path 完成")


if __name__ == '__main__':
    main()
