import os
import sys
from dotenv import load_dotenv
load_dotenv('.env')

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.db.connection import get_db_fresh

conn = None
try:
    conn = get_db_fresh()
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
finally:
    if conn and not conn.closed:
        conn.close()
