"""RSS 采集器 - 从 RSSHub 获取财经资讯"""
import os
import time
import json
import argparse
import requests
import feedparser
from datetime import datetime
import psycopg2
from psycopg2.extras import execute_values
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(BASE_DIR, '.env'))

RSSHUB_BASE = os.getenv('RSSHUB_BASE_URL', 'http://rsshub.app')


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'), user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'), dbname=os.getenv('POSTGRES_DB'),
    )


def fetch_feed(feed_url: str, source_id: int) -> list:
    """获取单个 feed"""
    try:
        url = f"{RSSHUB_BASE}{feed_url}" if feed_url.startswith('/') else feed_url
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        rows = []
        for entry in feed.entries:
            rows.append((
                source_id,
                entry.get('title', ''),
                entry.get('summary', entry.get('description', '')),
                entry.get('link', ''),
                datetime.strptime(entry.get('published', ''), '%a, %d %b %Y %H:%M:%S %Z') if entry.get('published') else None,
            ))
        return rows
    except Exception as e:
        logger.error(f"fetch failed {feed_url}: {e}")
        return []


def store_signals(rows: list):
    if not rows:
        return
    conn = get_db(); cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO raw_signals (source_id, title, content, url, pub_time)
        VALUES %s
        ON CONFLICT (source_id, url) DO NOTHING;
    """, rows)
    conn.commit()
    cur.close(); conn.close()
    logger.info(f"stored {len(rows)} signals")


def main(once=False):
    log_file = os.path.join(BASE_DIR, 'logs', 'collector.log')
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger.add(log_file, rotation='100 MB')
    
    # 从数据库获取 feeds
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, feed_url FROM feed_sources WHERE active=TRUE;")
    feeds = cur.fetchall()
    cur.close(); conn.close()
    
    logger.info(f"Loaded {len(feeds)} feeds")
    
    while True:
        for source_id, feed_url in feeds:
            rows = fetch_feed(feed_url, source_id)
            if rows:
                store_signals(rows)
            time.sleep(2)
        
        if once:
            return
        time.sleep(300)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='采集一次后退出')
    args = parser.parse_args()
    main(once=args.once)
