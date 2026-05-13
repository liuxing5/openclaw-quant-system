"""LLM 信号提取 - 消费 raw_signals -> 写入 extracted_recommendations"""
import os
import json
import time
import argparse
import threading
import concurrent.futures
from openai import OpenAI
import psycopg2
from psycopg2.extras import RealDictCursor
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

PRIMARY_API_KEY = os.getenv('LLM_API_KEY') or os.getenv('DEEPSEEK_API_KEY') or os.getenv('OPENAI_API_KEY')
PRIMARY_BASE_URL = os.getenv('LLM_BASE_URL') or os.getenv('DEEPSEEK_BASE_URL') or os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
PRIMARY_MODEL = os.getenv('LLM_MODEL') or os.getenv('DEEPSEEK_MODEL') or os.getenv('OPENAI_MODEL', 'gpt-4o-mini')

BACKUP_API_KEY = os.getenv('BACKUP_API_KEY') or os.getenv('DEEPSEEK_API_KEY') or os.getenv('OPENAI_API_KEY')
BACKUP_BASE_URL = os.getenv('DEEPSEEK_BASE_URL') or os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
BACKUP_MODEL = os.getenv('DEEPSEEK_MODEL') or os.getenv('OPENAI_MODEL', 'gpt-4o-mini')

CONCURRENCY = int(os.getenv('LLM_CONCURRENCY', 5))

with open(os.path.join(BASE_DIR, 'extract_v1.txt')) as f:
    PROMPT_TPL = f.read()

_thread_local = threading.local()


def get_client():
    """每个线程独立的 OpenAI client"""
    if not hasattr(_thread_local, 'client'):
        _thread_local.client = OpenAI(api_key=PRIMARY_API_KEY, base_url=PRIMARY_BASE_URL)
        _thread_local.model = PRIMARY_MODEL
        _thread_local.errors = 0
    return _thread_local.client, _thread_local.model


def call_llm(prompt: str) -> dict:
    """LLM 调用，带重试和模型切换"""
    for attempt in range(2):
        client, model = get_client()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1500,
                response_format={"type": "json_object"},
                timeout=30,
            )
            _thread_local.errors = 0
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            _thread_local.errors += 1
            logger.warning(f"LLM 调用失败 (线程{threading.current_thread().name}, 第{attempt+1}次): {e}")
            if _thread_local.errors >= 2 and model == PRIMARY_MODEL:
                logger.warning(f"切换到备用模型: {BACKUP_MODEL}")
                _thread_local.client = OpenAI(api_key=BACKUP_API_KEY, base_url=BACKUP_BASE_URL)
                _thread_local.model = BACKUP_MODEL
                _thread_local.errors = 0
            if attempt < 1:
                time.sleep(1)
    return {}


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
        sslmode='require',
    )


def fetch_pending(limit=20):
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    limit_int = int(limit)
    cur.execute("""
        SELECT r.id, r.title, r.content, r.pub_time,
               r.source_name, r.source_tier,
               CASE
                   WHEN POSITION('新闻' IN r.source_name) > 0
                     OR POSITION('快讯' IN r.source_name) > 0
                     OR POSITION('电报' IN r.source_name) > 0 THEN 'news'
                   WHEN POSITION('研报' IN r.source_name) > 0
                     OR POSITION('调研' IN r.source_name) > 0 THEN 'research'
                   WHEN POSITION('涨停' IN r.source_name) > 0
                     OR POSITION('龙虎榜' IN r.source_name) > 0 THEN 'lhb'
                   WHEN POSITION('概念' IN r.source_name) > 0 THEN 'concept'
                   ELSE 'news'
               END AS category
        FROM raw_signals r
        WHERE NOT EXISTS (
            SELECT 1 FROM extracted_recommendations e WHERE e.raw_signal_id=r.id
        )
        AND r.source_name NOT IN ('AKShare-龙虎榜', 'AKShare-涨停板', 'AKShare-机构调研')
        AND (
            r.title ~ '[0-9]{6}'
            OR r.content ~ '[0-9]{6}'
            OR EXISTS (
                SELECT 1 FROM stock_basic_info sb
                WHERE LENGTH(sb.stock_name) >= 2
                  AND (POSITION(sb.stock_name IN r.title) > 0
                       OR POSITION(sb.stock_name IN r.content) > 0)
            )
        )
        AND r.fetch_time > NOW() - INTERVAL '48 hours'
        ORDER BY r.source_tier ASC, r.fetch_time DESC
        LIMIT %s;
    """, (limit_int,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows


def store_extraction(raw_id: int, source_name: str, pub_time, items: list):
    if not items:
        return
    conn = get_db(); cur = conn.cursor()
    stored = 0
    for it in items:
        ts_code = it.get('ts_code')
        if not ts_code:
            logger.warning(f"跳过无 ts_code 的结果: {it.get('stock_name', 'unknown')}")
            continue
        cur.execute("""
            INSERT INTO extracted_recommendations
            (raw_signal_id, source_name, ts_code, stock_name, recommendation_type,
             strength, logic_category, logic_summary, target_price, stop_loss,
             time_horizon, raw_excerpt, confidence, pub_time)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING;
        """, (
            raw_id, source_name,
            ts_code, it.get('stock_name'),
            it.get('recommendation_type'), it.get('strength'),
            it.get('logic_category'), it.get('logic_summary'),
            it.get('target_price'), it.get('stop_loss'),
            it.get('time_horizon'), it.get('raw_excerpt'),
            it.get('confidence'), pub_time,
        ))
        stored += 1
    conn.commit()
    cur.close(); conn.close()
    return stored


def mark_signal_processed(raw_id, source_name, pub_time):
    """标记信号已处理，避免 LLM 空返回导致无限重试循环"""
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO extracted_recommendations
        (raw_signal_id, source_name, ts_code, stock_name, recommendation_type,
         strength, logic_summary, pub_time)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING;
    """, (raw_id, source_name, 'SKIP', 'SKIP', 'skip', 0, 'llm_no_result', pub_time))
    conn.commit()
    cur.close(); conn.close()


def process_one(row):
    safe_title = (row['title'] or '').replace('{', '{{').replace('}', '}}')
    safe_content = ((row['content'] or '')[:4000]).replace('{', '{{').replace('}', '}}')
    prompt = PROMPT_TPL.format(
        source_category=row['category'], source_tier=row['source_tier'],
        pub_time=row['pub_time'], title=safe_title,
        content=safe_content,
    )
    n = 0
    try:
        result = call_llm(prompt)

        if not result:
            logger.warning(f"raw_id={row['id']}: LLM 返回空结果，标记已处理避免无限重试")
            mark_signal_processed(row['id'], row['source_name'], row['pub_time'])
            return row['id'], 0

        is_recommendation = result.get('is_recommendation', False)
        items_raw = result.get('items', [])

        if not is_recommendation and items_raw:
            items = [it for it in items_raw
                     if it.get('recommendation_type') in ('buy', 'strong_buy')]
            if items:
                logger.debug(f"raw_id={row['id']}: 容错保留 {len(items)} 个明确 buy 信号")
        elif is_recommendation:
            items = items_raw
        else:
            items = []

        n = store_extraction(row['id'], row['source_name'], row['pub_time'], items)
        if not n:
            mark_signal_processed(row['id'], row['source_name'], row['pub_time'])
        logger.info(f"raw_id={row['id']} -> {n or 0} signals (is_recommendation={is_recommendation}, items_raw={len(items_raw)})")
    except Exception as e:
        logger.error(f"extract failed for {row['id']}: {e}")
        mark_signal_processed(row['id'], row['source_name'], row['pub_time'])
    return row['id'], n


def main(once=False):
    log_file = os.path.join(BASE_DIR, 'logs', 'extractor.log')
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger.add(log_file, rotation='100 MB')

    logger.info("=" * 50)
    logger.info(f"主模型: {PRIMARY_MODEL}")
    logger.info(f"备用模型: {BACKUP_MODEL}")
    logger.info(f"并发数: {CONCURRENCY}")
    logger.info("=" * 50)

    MAX_BATCH = 500
    total_processed = 0

    while total_processed < MAX_BATCH:
        limit = 5 if once else 50
        rows = fetch_pending(limit)
        if not rows:
            logger.info("无待处理数据，退出")
            break

        logger.info(f"本次处理 {len(rows)} 条信号")
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
            futures = {executor.submit(process_one, r): r for r in rows}
            for future in concurrent.futures.as_completed(futures):
                try:
                    rid, n = future.result()
                    total_processed += 1
                except Exception as e:
                    logger.error(f"线程异常: {e}")
                    total_processed += 1

        if once and total_processed > 0:
            break

    logger.info(f"提取完成，处理 {total_processed} 条")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='处理完当前批次后退出')
    args = parser.parse_args()
    main(once=args.once)
