import psycopg2
import os
from dotenv import load_dotenv
load_dotenv('.env')
conn = psycopg2.connect(
    host=os.getenv('POSTGRES_HOST'),
    port=int(os.getenv('POSTGRES_PORT') or '5432'),
    user=os.getenv('POSTGRES_USER'),
    password=os.getenv('POSTGRES_PASSWORD'),
    dbname=os.getenv('POSTGRES_DB'),
    sslmode='require'
)
cur = conn.cursor()

cur.execute('SELECT ts_code FROM stock_basic_info WHERE list_date IS NULL')
missing_codes = [row[0] for row in cur.fetchall()]

prefix_counts = {}
for code in missing_codes:
    prefix = code[:3]
    prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1

print(f'待填充股票数: {len(missing_codes)}')
print('代码前缀分布:')
for prefix, count in sorted(prefix_counts.items()):
    print(f'  {prefix}: {count}只')

print('\n部分待填充代码:')
for code in missing_codes[:15]:
    print(f'  {code}')

cur.close()
conn.close()