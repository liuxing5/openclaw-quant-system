"""从 data.md 解析推荐数据，查询当前行情并计算涨跌幅"""
import requests
import json

# Parse the markdown table
stocks = []
with open('strategies/data.md', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Skip header lines (first 2 lines are header and separator)
for line in lines[2:]:
    line = line.strip()
    if not line or not line.startswith('|'):
        continue
    cols = [c.strip() for c in line.split('|')]
    # Remove empty first/last
    cols = [c for c in cols if c]
    if len(cols) < 20:
        continue
    
    try:
        stock = {
            'id': cols[0],
            'snapshot_date': cols[1],
            'ts_code': cols[2],
            'stock_name': cols[3],
            'final_score': float(cols[9]) if cols[9] else 0,
            'selected': cols[10] == 'true',
            'entry_low': float(cols[14]) if cols[14] and cols[14] != 'null' else None,
            'entry_high': float(cols[15]) if cols[15] and cols[15] != 'null' else None,
            'source': cols[18],
            'run_mode': cols[19],
        }
        if stock['selected']:
            stocks.append(stock)
    except (ValueError, IndexError):
        continue

print(f"Total selected stocks: {len(stocks)}", flush=True)

# Group by date
from collections import defaultdict
by_date = defaultdict(list)
for s in stocks:
    by_date[s['snapshot_date']].append(s)

for date in sorted(by_date.keys(), reverse=True):
    items = by_date[date]
    print(f"\n{'='*80}", flush=True)
    print(f"Date: {date} ({len(items)} stocks)", flush=True)
    print(f"{'='*80}", flush=True)
    
    # Calculate recommendation price
    for s in items:
        if s['entry_low'] and s['entry_high']:
            s['rec_price'] = (s['entry_low'] + s['entry_high']) / 2
        elif s['entry_low']:
            s['rec_price'] = s['entry_low']
        elif s['entry_high']:
            s['rec_price'] = s['entry_high']
        else:
            s['rec_price'] = None
    
    # Show stocks with valid entry prices
    valid = [s for s in items if s['rec_price']]
    invalid = [s for s in items if not s['rec_price']]
    
    print(f"  With entry price: {len(valid)}", flush=True)
    print(f"  Without entry price: {len(invalid)}", flush=True)
    
    if invalid:
        print(f"  Missing entry: {', '.join(s['ts_code'] for s in invalid)}", flush=True)
