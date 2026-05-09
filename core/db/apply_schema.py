"""用 Python 执行 schema.sql（替代 psql）"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db
from core.utils.env import load_project_env

load_project_env()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

conn = get_db()
conn.autocommit = True

with open(os.path.join(BASE_DIR, 'schema.sql'), 'r') as f:
    sql = f.read()

cur = conn.cursor()
cur.execute(sql)
cur.close()
conn.close()
print("Schema applied successfully")
