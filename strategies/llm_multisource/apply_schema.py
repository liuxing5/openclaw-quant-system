"""用 Python 执行 schema.sql（替代 psql）"""
import os
import psycopg2
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

port_str = os.getenv('POSTGRES_PORT') or '5432'

conn = psycopg2.connect(
    host=os.getenv('POSTGRES_HOST'),
    port=int(port_str),
    user=os.getenv('POSTGRES_USER'),
    password=os.getenv('POSTGRES_PASSWORD'),
    dbname=os.getenv('POSTGRES_DB'),
)
conn.autocommit = True

with open(os.path.join(BASE_DIR, 'schema.sql'), 'r') as f:
    sql = f.read()

cur = conn.cursor()
cur.execute(sql)
cur.close()
conn.close()
print("Schema applied successfully")
