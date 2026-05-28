"""排查v6回测问题 - 查询实际K线数据"""
import sys, os
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

# 重定向输出到文件
output_file = open('kline_check_output.txt', 'w', encoding='utf-8')
class Tee:
    def __init__(self, *files):
        self.files = files
    def write(self, data):
        for f in self.files:
            f.write(data)
    def flush(self):
        for f in self.files:
            f.flush()
sys.stdout = Tee(sys.__stdout__, output_file)

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

import psycopg2
from psycopg2.extras import RealDictCursor

conn = psycopg2.connect(
    host='aws-1-ap-northeast-1.pooler.supabase.com',
    port=5432,
    user='postgres.qoakbxswwjqfsgbcgepr',
    password='wYFBB91zViSrk2vl',
    dbname='postgres',
    sslmode='require'
)

# 需要排查的股票
stocks = {
    '000029.SZ': ('2026-05-05', '2026-05-16'),   # 主升浪
    '000070.SZ': ('2026-04-07', '2026-04-14'),   # 9日进10日跌?
    '000062.SZ': ('2026-04-10', '2026-05-05'),   # 13日进23日卖,27日28日还涨
    '000026.SZ': ('2026-04-20', '2026-05-25'),   # 23日进,一直涨到27元
    '000155.SZ': ('2026-04-15', '2026-05-10'),   # 17日进,29日最高
    '000030.SZ': ('2026-03-30', '2026-04-07'),   # 2日买入,已下跌2天
    '000019.SZ': ('2026-04-11', '2026-04-20'),   # 15日买入,已下跌2天
    '000158.SZ': ('2026-04-24', '2026-05-05'),   # 下跌第2日买入
    '000420.SZ': ('2026-05-12', '2026-05-16'),   # 当日买卖T+1违规
    '000065.SZ': ('2026-04-07', '2026-04-12'),   # 8日涨8%,9日跌7%
}

for ts_code, (start, end) in stocks.items():
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT trade_date, open, high, low, close, volume, amount, pct_chg
        FROM daily_quotes
        WHERE ts_code = %s AND trade_date BETWEEN %s AND %s
        ORDER BY trade_date
    """, (ts_code, start, end))
    rows = cur.fetchall()
    print(f"\n{'='*80}")
    print(f"  {ts_code}  ({start} ~ {end})")
    print(f"{'='*80}")
    print(f"  {'日期':>12}  {'开盘':>8}  {'最高':>8}  {'最低':>8}  {'收盘':>8}  {'涨跌幅':>8}  {'成交量':>12}")
    print(f"  {'-'*72}")
    for r in rows:
        print(f"  {r['trade_date']:>12}  {r['open']:>8.2f}  {r['high']:>8.2f}  {r['low']:>8.2f}  {r['close']:>8.2f}  {r['pct_chg']:>+8.2f}%  {r['volume']:>12.0f}")
    cur.close()

conn.close()
print("\n\nDONE")
