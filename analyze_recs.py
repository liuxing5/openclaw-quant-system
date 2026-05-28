"""从 data.md 解析推荐数据，每日每只股票一条记录，输出推荐价和当前价对比"""
from collections import defaultdict
import requests

# Read the file
with open('strategies/data.md', 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.strip().split('\n')

stocks = []
for line in lines[2:]:
    line = line.strip()
    if not line or not line.startswith('|'):
        continue
    
    parts = line.split('|')
    parts = parts[1:-1]
    
    if len(parts) < 22:
        first_18 = parts[:18]
        last_3 = parts[-3:]
        sources = '|'.join(parts[18:-3])
        parts = first_18 + [sources] + last_3
    
    if len(parts) < 22:
        continue
    
    try:
        parts = [p.strip() for p in parts]
        
        entry_low = parts[13] if parts[13] != 'null' else None
        entry_high = parts[14] if parts[14] != 'null' else None
        
        stock = {
            'id': parts[0],
            'snapshot_date': parts[1],
            'ts_code': parts[2],
            'stock_name': parts[3],
            'final_score': float(parts[9]) if parts[9] else 0,
            'selected': parts[11] == 'true',
            'entry_low': float(entry_low) if entry_low else None,
            'entry_high': float(entry_high) if entry_high else None,
            'source': parts[21],
            'run_mode': parts[20],
        }
        
        if stock['selected']:
            stocks.append(stock)
    except (ValueError, IndexError):
        continue

# Calculate recommendation price
for s in stocks:
    if s['entry_low'] and s['entry_high']:
        s['rec_price'] = (s['entry_low'] + s['entry_high']) / 2
    elif s['entry_low']:
        s['rec_price'] = s['entry_low']
    elif s['entry_high']:
        s['rec_price'] = s['entry_high']
    else:
        s['rec_price'] = None

# Convert ts_code to Sina API format
def code_to_sina(ts_code):
    code, exchange = ts_code.split('.')
    if exchange == 'SH':
        return f'sh{code}'
    elif exchange == 'SZ':
        return f'sz{code}'
    return ts_code

# Fetch current prices from Sina Finance API
def fetch_sina_prices(codes):
    sina_codes = [code_to_sina(c) for c in codes]
    url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
    headers = {'Referer': 'https://finance.sina.com.cn'}
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'gbk'
        
        results = {}
        for line in resp.text.strip().split('\n'):
            if '=' in line and '"' in line:
                code_part = line.split('=')[0].strip()
                data_part = line.split('"')[1]
                if data_part:
                    fields = data_part.split(',')
                    sina_code = code_part.replace('var hq_str_', '')
                    if sina_code.startswith('sh'):
                        ts = f'{sina_code[2:]}.SH'
                    elif sina_code.startswith('sz'):
                        ts = f'{sina_code[2:]}.SZ'
                    else:
                        ts = sina_code
                    
                    try:
                        current = float(fields[3])
                        name = fields[0]
                        results[ts] = {'name': name, 'current': current}
                    except (ValueError, IndexError):
                        pass
        return results
    except Exception as e:
        print(f"Sina API error: {e}")
        return {}

# Get all unique codes with valid rec_price
valid_stocks = [s for s in stocks if s['rec_price']]
all_codes = list(set(s['ts_code'] for s in valid_stocks))
prices = fetch_sina_prices(all_codes)

# Output: one record per stock per day
print(f"{'日期':<12} {'代码':<12} {'名称':<8} {'推荐价':<10} {'当前价':<10} {'涨跌幅':<10} {'状态':<8} {'来源':<20} {'策略'}", flush=True)
print("-" * 120, flush=True)

# Sort by date desc, then by final_score desc
valid_stocks.sort(key=lambda x: (x['snapshot_date'], x['final_score']), reverse=True)

for s in valid_stocks:
    code = s['ts_code']
    name = s.get('stock_name', '') or ''
    if code in prices:
        name = prices[code]['name'] or name
    
    rec = s['rec_price']
    
    if code in prices:
        now = prices[code]['current']
        chg = (now - rec) / rec * 100
        chg_str = f"{chg:+.2f}%"
        status = "UP" if chg > 0 else ("DOWN" if chg < 0 else "FLAT")
        now_str = f"{now:.2f}"
    else:
        now_str = "N/A"
        chg_str = "N/A"
        status = "NO_DATA"
    
    source = s.get('source', '')[:20]
    run_mode = s.get('run_mode', '')
    
    print(f"{s['snapshot_date']:<12} {code:<12} {name[:8]:<8} {rec:<10.2f} {now_str:<10} {chg_str:<10} {status:<8} {source:<20} {run_mode}", flush=True)

print("-" * 120, flush=True)
print(f"总计: {len(valid_stocks)} 条记录", flush=True)
