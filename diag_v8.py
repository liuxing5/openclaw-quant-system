"""诊断v7回测问题：逐笔交易K线数据验证"""
import sys, os
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from strategies.meta_strategy.db_data_adapter import get_daily_quotes
from datetime import date

def show_kline(ts_code, start, end):
    s = date(*[int(x) for x in start.split('-')])
    e = date(*[int(x) for x in end.split('-')])
    df = get_daily_quotes(ts_code, s, e)
    print(f"\n{'='*80}")
    print(f"{ts_code} ({start} ~ {end})")
    print(f"{'='*80}")
    if df.empty:
        print("  无数据!")
        return
    for _, r in df.iterrows():
        d = str(r['trade_date'])[:10]
        o = float(r['open'])
        h = float(r['high'])
        l = float(r['low'])
        c = float(r['close'])
        pct = float(r.get('pct_chg', 0))
        vol = float(r.get('volume', 0))
        print(f"  {d}: open={o:.2f} high={h:.2f} low={l:.2f} close={c:.2f} pct={pct:+.2f}% vol={vol:,.0f}")

show_kline('000030.SZ', '2026-03-30', '2026-04-07')
show_kline('000045.SZ', '2026-03-30', '2026-04-07')
show_kline('000155.SZ', '2026-03-30', '2026-04-07')
show_kline('000423.SZ', '2026-03-30', '2026-04-07')
show_kline('000026.SZ', '2026-04-07', '2026-05-15')
show_kline('000415.SZ', '2026-04-28', '2026-05-07')
show_kline('000408.SZ', '2026-04-18', '2026-04-25')
