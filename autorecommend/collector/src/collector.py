"""财经资讯采集器 - 四层数据采集架构
Layer 1: BaoStock/AKShare 结构化数据（零依赖，最稳）
Layer 2: 自建 RSSHub（主力文本源）
Layer 3: HTTP 直接抓取（兜底）
Layer 4: Playwright 浏览器自动化（最后手段）
"""
import os
import time
import json
import argparse
import hashlib
from datetime import datetime, date, timedelta
import requests
import feedparser
import httpx
from bs4 import BeautifulSoup
import pandas as pd
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(BASE_DIR, '.env'))

RSSHUB_BASE = os.getenv('RSSHUB_BASE_URL', 'http://localhost:1200')

import psycopg2
from psycopg2.extras import execute_values


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'), user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'), dbname=os.getenv('POSTGRES_DB'),
    )


def ensure_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS raw_signals (
        id BIGSERIAL PRIMARY KEY,
        source_id INT,
        source_name VARCHAR(100),
        source_tier INT,
        title TEXT,
        content TEXT,
        url TEXT,
        pub_time TIMESTAMPTZ,
        fetch_time TIMESTAMPTZ DEFAULT NOW(),
        content_hash VARCHAR(64) UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_raw_pub_time ON raw_signals(pub_time DESC);
    CREATE INDEX IF NOT EXISTS idx_raw_fetch_time ON raw_signals(fetch_time DESC);
    CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_signals(source_id);
    """
    conn = get_db(); cur = conn.cursor()
    cur.execute(sql); conn.commit(); cur.close(); conn.close()


def content_hash(title: str, content: str) -> str:
    return hashlib.md5(f"{title}{content[:500]}".encode()).hexdigest()


# ============================================================
# Layer 1: BaoStock / AKShare 结构化数据
# ============================================================

def layer1_baostock_research():
    """BaoStock 获取研报/公告类数据"""
    rows = []
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            return rows
        
        today_str = date.today().strftime('%Y-%m-%d')
        
        rs = bs.query_history_k_data_plus(
            "sh.000300",
            "date,code,open,high,low,close,volume,amount,turn",
            start_date=today_str, end_date=today_str,
            frequency="d", adjustflag="3"
        )
        bs.logout()
        
        if rs.error_code == '0' and rs.next():
            row = rs.get_row_data()
            rows.append({
                'source_name': 'BaoStock-大盘行情',
                'source_tier': 1,
                'title': f'沪深300指数 {row[0]} 行情',
                'content': f'开盘:{row[2]} 最高:{row[3]} 最低:{row[4]} 收盘:{row[5]} 成交量:{row[6]} 成交额:{row[7]}',
                'url': '',
                'pub_time': datetime.strptime(row[0], '%Y-%m-%d') if row[0] else None,
            })
    except Exception as e:
        logger.warning(f"BaoStock 结构化数据失败: {e}")
    return rows


def layer1_akshare_news():
    """AKShare 获取财经新闻/快讯"""
    rows = []
    try:
        import akshare as ak
        
        logger.info("Layer1: 尝试 AKShare 获取财经新闻...")
        
        df = ak.stock_news_em(symbol="全部")
        if df is not None and len(df) > 0:
            for _, r in df.head(50).iterrows():
                rows.append({
                    'source_name': 'AKShare-东方财富新闻',
                    'source_tier': 1,
                    'title': str(r.get('新闻标题', '')),
                    'content': str(r.get('新闻内容', ''))[:1000],
                    'url': str(r.get('新闻链接', '')),
                    'pub_time': pd.to_datetime(r.get('发布时间')) if pd.notna(r.get('发布时间')) else None,
                })
            logger.info(f"AKShare 新闻获取到 {len(rows)} 条")
    except Exception as e:
        logger.warning(f"AKShare 新闻失败: {e}")
    
    return rows


def layer1_akshare_hot_rank():
    """AKShare 获取热门股票/概念"""
    rows = []
    try:
        import akshare as ak
        
        logger.info("Layer1: 尝试 AKShare 获取热门概念...")
        
        df = ak.stock_hot_rank_em()
        if df is not None and len(df) > 0:
            for _, r in df.head(30).iterrows():
                rows.append({
                    'source_name': 'AKShare-热门股票',
                    'source_tier': 1,
                    'title': f"热门: {r.get('股票名称', '')} ({r.get('股票代码', '')})",
                    'content': f"排名:{r.get('排名','')} 涨跌幅:{r.get('涨跌幅','')} 最新价:{r.get('最新价','')}",
                    'url': '',
                    'pub_time': datetime.now(),
                })
            logger.info(f"AKShare 热门股票获取到 {len(rows)} 条")
    except Exception as e:
        logger.warning(f"AKShare 热门股票失败: {e}")
    
    return rows


def layer1_akshare_lhb():
    """AKShare 获取龙虎榜数据"""
    rows = []
    try:
        import akshare as ak
        
        today_str = date.today().strftime('%Y%m%d')
        df = ak.stock_lhb_detail_em(start_date=today_str, end_date=today_str)
        if df is not None and len(df) > 0:
            for _, r in df.head(20).iterrows():
                rows.append({
                    'source_name': 'AKShare-龙虎榜',
                    'source_tier': 1,
                    'title': f"龙虎榜: {r.get('名称','')} ({r.get('代码','')})",
                    'content': f"上榜原因:{r.get('解读','')} 买入:{r.get('买入额','')} 卖出:{r.get('卖出额','')}",
                    'url': '',
                    'pub_time': datetime.now(),
                })
            logger.info(f"AKShare 龙虎榜获取到 {len(rows)} 条")
    except Exception as e:
        logger.warning(f"AKShare 龙虎榜失败: {e}")
    
    return rows


def layer1_akshare_concept():
    """AKShare 获取概念板块异动"""
    rows = []
    try:
        import akshare as ak
        
        df = ak.stock_board_concept_name_em()
        if df is not None and len(df) > 0:
            for _, r in df.head(20).iterrows():
                rows.append({
                    'source_name': 'AKShare-概念板块',
                    'source_tier': 1,
                    'title': f"概念: {r.get('板块名称','')}",
                    'content': f"涨跌幅:{r.get('涨跌幅','')} 领涨股:{r.get('领涨股票','')} 领涨股涨跌幅:{r.get('领涨股票-涨跌幅','')}",
                    'url': '',
                    'pub_time': datetime.now(),
                })
            logger.info(f"AKShare 概念板块获取到 {len(rows)} 条")
    except Exception as e:
        logger.warning(f"AKShare 概念板块失败: {e}")
    
    return rows


def layer1_all():
    """执行所有 Layer 1 数据源"""
    all_rows = []
    
    logger.info("=" * 50)
    logger.info("Layer 1: BaoStock/AKShare 结构化数据")
    logger.info("=" * 50)
    
    for fetcher in [layer1_akshare_news, layer1_akshare_hot_rank, layer1_akshare_lhb, layer1_akshare_concept, layer1_baostock_research]:
        try:
            rows = fetcher()
            if rows:
                all_rows.extend(rows)
                logger.info(f"  {fetcher.__name__}: +{len(rows)} 条")
        except Exception as e:
            logger.warning(f"  {fetcher.__name__} 异常: {e}")
    
    logger.info(f"Layer 1 总计: {len(all_rows)} 条")
    return all_rows


# ============================================================
# Layer 2: RSSHub（自建实例）
# ============================================================

def layer2_rsshub():
    """从自建 RSSHub 获取 RSS 数据"""
    rows = []
    
    logger.info("=" * 50)
    logger.info("Layer 2: RSSHub 自建实例")
    logger.info("=" * 50)
    
    feeds = [
        {'route': '/cls/telegraph', 'name': '财联社电报', 'tier': 1},
        {'route': '/wallstreetcn/news/global', 'name': '华尔街见闻', 'tier': 1},
        {'route': '/xueqiu/today_topic', 'name': '雪球热门', 'tier': 2},
    ]
    
    for feed in feeds:
        try:
            url = f"{RSSHUB_BASE}{feed['route']}"
            logger.info(f"  请求: {url}")
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            
            for entry in parsed.entries[:20]:
                rows.append({
                    'source_name': feed['name'],
                    'source_tier': feed['tier'],
                    'title': entry.get('title', ''),
                    'content': entry.get('summary', entry.get('description', ''))[:1000],
                    'url': entry.get('link', ''),
                    'pub_time': datetime.strptime(entry.get('published', ''), '%a, %d %b %Y %H:%M:%S %Z') if entry.get('published') else None,
                })
            logger.info(f"  {feed['name']}: +{len(parsed.entries)} 条")
        except Exception as e:
            logger.warning(f"  RSSHub {feed['name']} 失败: {e}")
    
    logger.info(f"Layer 2 总计: {len(rows)} 条")
    return rows


# ============================================================
# Layer 3: HTTP 直接抓取（兜底）
# ============================================================

def layer3_cls_api():
    """财联社 API 直接抓取"""
    rows = []
    try:
        logger.info("Layer3: 尝试财联社 API...")
        
        url = "https://www.cls.cn/nodeapi/updateTelegraphList"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.cls.cn/telegraph',
        }
        params = {
            'app': 'CailianpressWeb',
            'os': 'web',
            'sv': '7.7.5',
            'rn': '20',
        }
        
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get('data') and data['data'].get('roll_data'):
            for item in data['data']['roll_data']:
                rows.append({
                    'source_name': '财联社-API',
                    'source_tier': 1,
                    'title': item.get('title', '') or item.get('content', '')[:50],
                    'content': item.get('content', '')[:1000],
                    'url': f"https://www.cls.cn/detail/{item.get('id', '')}",
                    'pub_time': datetime.fromtimestamp(item.get('ctime', 0)) if item.get('ctime') else None,
                })
            logger.info(f"  财联社 API: +{len(rows)} 条")
    except Exception as e:
        logger.warning(f"  财联社 API 失败: {e}")
    
    return rows


def layer3_eastmoney_news():
    """东方财富快讯直接抓取"""
    rows = []
    try:
        logger.info("Layer3: 尝试东方财富快讯...")
        
        url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
        params = {
            'sr': '-1',
            'page_size': '20',
            'page_index': '1',
            'ann_type': 'A',
        }
        
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get('data') and data['data'].get('list'):
            for item in data['data']['list']:
                rows.append({
                    'source_name': '东方财富-公告',
                    'source_tier': 2,
                    'title': item.get('title', ''),
                    'content': item.get('content', '')[:1000] if item.get('content') else '',
                    'url': f"https://data.eastmoney.com/notice/{item.get('art_code', '')}.html",
                    'pub_time': datetime.strptime(item.get('notice_date', ''), '%Y-%m-%d %H:%M:%S') if item.get('notice_date') else None,
                })
            logger.info(f"  东方财富快讯: +{len(rows)} 条")
    except Exception as e:
        logger.warning(f"  东方财富快讯失败: {e}")
    
    return rows


def layer3_all():
    """执行所有 Layer 3 数据源"""
    all_rows = []
    
    logger.info("=" * 50)
    logger.info("Layer 3: HTTP 直接抓取")
    logger.info("=" * 50)
    
    for fetcher in [layer3_cls_api, layer3_eastmoney_news]:
        try:
            rows = fetcher()
            if rows:
                all_rows.extend(rows)
        except Exception as e:
            logger.warning(f"  {fetcher.__name__} 异常: {e}")
    
    logger.info(f"Layer 3 总计: {len(all_rows)} 条")
    return all_rows


# ============================================================
# Layer 4: Playwright 浏览器自动化（最后手段）
# ============================================================

def layer4_playwright():
    """Playwright 浏览器自动化 - 仅用于高价值且其他方式都失败的源"""
    rows = []
    
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("Layer 4: Playwright 未安装，跳过")
        return rows
    
    logger.info("=" * 50)
    logger.info("Layer 4: Playwright 浏览器自动化")
    logger.info("=" * 50)
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            page.goto("https://www.cls.cn/telegraph", wait_until='domcontentloaded', timeout=30000)
            time.sleep(3)
            
            items = page.query_selector_all('.telegraph-content-box')
            for item in items[:20]:
                try:
                    text = item.inner_text()
                    if text.strip():
                        rows.append({
                            'source_name': '财联社-Playwright',
                            'source_tier': 1,
                            'title': text[:50],
                            'content': text[:1000],
                            'url': '',
                            'pub_time': datetime.now(),
                        })
                except:
                    continue
            
            browser.close()
            logger.info(f"  Playwright 财联社: +{len(rows)} 条")
    except Exception as e:
        logger.warning(f"  Playwright 失败: {e}")
    
    logger.info(f"Layer 4 总计: {len(rows)} 条")
    return rows


# ============================================================
# 存储与去重
# ============================================================

def store_signals(rows: list):
    if not rows:
        return 0
    
    conn = get_db(); cur = conn.cursor()
    
    stored = 0
    for r in rows:
        h = content_hash(r.get('title', ''), r.get('content', ''))
        try:
            cur.execute("""
                INSERT INTO raw_signals 
                (source_name, source_tier, title, content, url, pub_time, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (content_hash) DO NOTHING;
            """, (
                r.get('source_name', ''),
                r.get('source_tier', 2),
                r.get('title', ''),
                r.get('content', ''),
                r.get('url', ''),
                r.get('pub_time'),
                h,
            ))
            if cur.rowcount > 0:
                stored += 1
        except Exception as e:
            logger.debug(f"存储失败: {e}")
    
    conn.commit()
    cur.close(); conn.close()
    logger.info(f"存储: {stored}/{len(rows)} 条新信号")
    return stored


def main(once=False):
    log_file = os.path.join(BASE_DIR, 'logs', 'collector.log')
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger.add(log_file, rotation='100 MB')
    
    ensure_tables()
    
    logger.info("=" * 60)
    logger.info("四层数据采集架构启动")
    logger.info("=" * 60)
    
    total = 0
    
    while True:
        # Layer 1: 结构化数据（最稳）
        layer1_rows = layer1_all()
        total += store_signals(layer1_rows)
        
        # Layer 2: RSSHub（如果 Layer 1 数据不足）
        if len(layer1_rows) < 20:
            layer2_rows = layer2_rsshub()
            total += store_signals(layer2_rows)
        
        # Layer 3: HTTP 直接抓取（如果前两层数据不足）
        if total < 30:
            layer3_rows = layer3_all()
            total += store_signals(layer3_rows)
        
        # Layer 4: Playwright（最后手段，如果数据仍然不足）
        if total < 10:
            layer4_rows = layer4_playwright()
            total += store_signals(layer4_rows)
        
        logger.info(f"本轮采集总计: {total} 条新信号")
        
        if once:
            return
        
        time.sleep(300)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='采集一次后退出')
    args = parser.parse_args()
    main(once=args.once)
