"""GHA 模式下手动写死源配置到数据库"""
import os
import psycopg2
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

SOURCES = [
    # === Existing 6 (AKShare) ===
    ('AKShare-财经新闻', 'akshare_news', 'news', 2, 1.0),
    ('AKShare-龙虎榜', 'akshare_lhb', 'lhb', 1, 1.2),
    ('AKShare-涨停板', 'akshare_zt_pool', 'lhb', 1, 1.2),
    ('AKShare-热点概念', 'akshare_concept_hot', 'concept', 2, 0.9),
    ('AKShare-个股研报', 'akshare_research', 'research', 1, 1.3),
    ('AKShare-机构调研', 'akshare_jgdy', 'research', 1, 1.1),
    # === Layer 1: Market Data ===
    ('MootDX-实时行情', 'mootdx_quotes', 'market', 1, 1.0),
    ('MootDX-五档盘口', 'mootdx_orderbook', 'market', 2, 0.7),
    ('MootDX-K线', 'mootdx_kline', 'market', 1, 0.8),
    ('Tencent-行情补充', 'tencent_quotes', 'market', 1, 0.9),
    ('THS-强势股', 'ths_strong_stocks', 'market', 1, 1.1),
    ('THS-概念标签', 'ths_concept_tags', 'concept', 2, 0.8),
    # === Layer 2: Research Reports ===
    ('东财-盈利预测', 'em_profit_forecast', 'research', 1, 1.2),
    ('THS-盈利预测', 'ths_profit_forecast', 'research', 1, 1.1),
    # === Layer 3: News ===
    ('财联社-电报', 'cls_telegraph', 'news', 1, 1.1),
    # === Layer 4: Fundamentals ===
    ('MootDX-财务数据', 'mootdx_fundamentals', 'fundamental', 1, 0.8),
    # === Layer 5: Announcements ===
    ('巨潮-公告', 'cninfo_announcements', 'announcement', 2, 0.7),
    ('MootDX-公告', 'mootdx_announcements', 'announcement', 2, 0.6),
]


def seed():
    port_str = os.getenv('POSTGRES_PORT') or '5432'
    conn = psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(port_str),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
    )
    cur = conn.cursor()
    for name, route, cat, tier, weight in SOURCES:
        cur.execute("""
            INSERT INTO feed_sources (name, route, category, tier, weight, poll_interval_sec)
            VALUES (%s, %s, %s, %s, %s, 86400)
            ON CONFLICT (name) DO UPDATE SET
                tier=EXCLUDED.tier, weight=EXCLUDED.weight;
        """, (name, route, cat, tier, weight))
    conn.commit()
    print(f"Seeded {len(SOURCES)} sources")
    cur.close()
    conn.close()


if __name__ == '__main__':
    seed()
