import yaml
import os
import psycopg2
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

feeds_path = os.path.join(BASE_DIR, 'configs', 'feeds', 'feeds.yaml')
with open(feeds_path) as f:
    cfg = yaml.safe_load(f)

conn = psycopg2.connect(
    host=os.getenv('POSTGRES_HOST'),
    user=os.getenv('POSTGRES_USER'),
    password=os.getenv('POSTGRES_PASSWORD'),
    dbname=os.getenv('POSTGRES_DB'),
)
cur = conn.cursor()

for s in cfg['sources']:
    cur.execute("""
        INSERT INTO feed_sources (name, route, category, tier, weight, poll_interval_sec)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            route=EXCLUDED.route, category=EXCLUDED.category,
            tier=EXCLUDED.tier, weight=EXCLUDED.weight,
            poll_interval_sec=EXCLUDED.poll_interval_sec;
    """, (s['name'], s['route'], s['category'], s['tier'], s['weight'], s['poll_interval_sec']))

conn.commit()
print(f"Loaded {len(cfg['sources'])} feeds")
