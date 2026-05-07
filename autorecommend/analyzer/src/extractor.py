"""LLM 信号提取 - 消费 raw_signals → 写入 extracted_recommendations"""
import os
import json
import time
import argparse
from openai import OpenAI
import psycopg2
from psycopg2.extras import RealDictCursor
from loguru import logger
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# 主模型配置
PRIMARY_API_KEY = os.getenv('LLM_API_KEY') or os.getenv('DEEPSEEK_API_KEY') or os.getenv('OPENAI_API_KEY')
PRIMARY_BASE_URL = os.getenv('LLM_BASE_URL') or os.getenv('DEEPSEEK_BASE_URL') or os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
PRIMARY_MODEL = os.getenv('LLM_MODEL') or os.getenv('DEEPSEEK_MODEL') or os.getenv('OPENAI_MODEL', 'gpt-4o-mini')

# 备用模型配置
BACKUP_API_KEY = os.getenv('DEEPSEEK_API_KEY') or os.getenv('OPENAI_API_KEY')
BACKUP_BASE_URL = os.getenv('DEEPSEEK_BASE_URL') or os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
BACKUP_MODEL = os.getenv('DEEPSEEK_MODEL') or os.getenv('OPENAI_MODEL', 'gpt-4o-mini')

# 当前使用的模型
current_api_key = PRIMARY_API_KEY
current_base_url = PRIMARY_BASE_URL
current_model = PRIMARY_MODEL
consecutive_errors = 0
MAX_ERRORS_BEFORE_SWITCH = 3

client = OpenAI(api_key=current_api_key, base_url=current_base_url)

with open(os.path.join(BASE_DIR, 'analyzer', 'prompts', 'extract_v1.txt')) as f:
    PROMPT_TPL = f.read()


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'), user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'), dbname=os.getenv('POSTGRES_DB'),
    )


def switch_to_backup():
    global client, current_api_key, current_base_url, current_model, consecutive_errors
    if current_model == BACKUP_MODEL:
        return
    logger.warning(f"切换到备用模型: {BACKUP_MODEL}")
    current_api_key = BACKUP_API_KEY
    current_base_url = BACKUP_BASE_URL
    current_model = BACKUP_MODEL
    client = OpenAI(api_key=current_api_key, base_url=current_base_url)
    consecutive_errors = 0


def switch_to_primary():
    global client, current_api_key, current_base_url, current_model, consecutive_errors
    if current_model == PRIMARY_MODEL:
        return
    logger.info(f"切换回主模型: {PRIMARY_MODEL}")
    current_api_key = PRIMARY_API_KEY
    current_base_url = PRIMARY_BASE_URL
    current_model = PRIMARY_MODEL
    client = OpenAI(api_key=current_api_key, base_url=current_base_url)
    consecutive_errors = 0


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def call_llm(prompt: str) -> dict:
    global consecutive_errors
    try:
        resp = client.chat.completions.create(
            model=current_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        consecutive_errors = 0
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        consecutive_errors += 1
        logger.warning(f"LLM 调用失败 (连续{consecutive_errors}次): {e}")
        if consecutive_errors >= MAX_ERRORS_BEFORE_SWITCH:
            switch_to_backup()
        raise


def fetch_pending(limit=20):
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT r.id, r.title, r.content, r.pub_time, r.source_id,
               s.category, s.tier, s.name AS source_name
        FROM raw_signals r JOIN feed_sources s ON r.source_id=s.id
        WHERE NOT EXISTS (
            SELECT 1 FROM extracted_recommendations e WHERE e.raw_signal_id=r.id
        )
        AND (r.title ~ '\d{6}' OR r.content ~ '\d{6}')
        ORDER BY r.fetch_time DESC LIMIT %s;
    """, (limit,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows


def store_extraction(raw_id: int, source_id: int, pub_time, items: list):
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
            (raw_signal_id, source_id, ts_code, stock_name, recommendation_type,
             strength, logic_category, logic_summary, target_price, stop_loss,
             time_horizon, raw_excerpt, confidence, pub_time)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
        """, (
            raw_id, source_id,
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


def process_one(row):
    prompt = PROMPT_TPL.format(
        source_category=row['category'], source_tier=row['tier'],
        pub_time=row['pub_time'], title=row['title'] or '',
        content=(row['content'] or '')[:4000],
    )
    try:
        result = call_llm(prompt)
        items = result.get('items', []) if result.get('is_recommendation') else []
        n = store_extraction(row['id'], row['source_id'], row['pub_time'], items)
        logger.info(f"raw_id={row['id']} → {n or 0} signals")
        if current_model != PRIMARY_MODEL:
            switch_to_primary()
    except Exception as e:
        logger.error(f"extract failed for {row['id']}: {e}")


def main(once=False):
    log_file = os.path.join(BASE_DIR, 'logs', 'extractor.log')
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger.add(log_file, rotation='100 MB')
    
    logger.info(f"启动提取器 - 主模型: {PRIMARY_MODEL}, 备用: {BACKUP_MODEL}")
    
    while True:
        rows = fetch_pending(20)
        if not rows:
            if once:
                logger.info("无待处理数据，退出")
                return
            time.sleep(30)
            continue
        for r in rows:
            process_one(r)
            time.sleep(0.5)
        if once:
            logger.info(f"处理完成，共 {len(rows)} 条")
            return
        time.sleep(5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='处理完当前批次后退出')
    args = parser.parse_args()
    main(once=args.once)
