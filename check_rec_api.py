"""Use Supabase REST API to query data (avoids connection pool issues)"""
import requests
import time
from collections import defaultdict

SUPABASE_URL = 'https://qoakbxswwjqfsgbcgepr.supabase.co'
SUPABASE_KEY = 'wYFBB91zViSrk2vl'

headers = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'count=exact',
}

# Step 1: Get recent recommendations
print("Fetching recent recommendations...", flush=True)
resp = requests.get(
    f'{SUPABASE_URL}/rest/v1/daily_candidates',
    headers=headers,
    params={
        'selected': 'eq.true',
        'order': 'snapshot_date.desc',
        'limit': '10',
        'select': 'snapshot_date,source,run_mode,ts_code,stock_name,final_score,entry_low,entry_high',
    },
    timeout=30,
)

if resp.status_code != 200:
    print(f"Error: {resp.status_code} - {resp.text}", flush=True)
    exit(1)

data = resp.json()
print(f"Got {len(data)} records", flush=True)

# Group by date/source/run_mode
from collections import defaultdict
groups = defaultdict(list)
for r in data:
    key = (r['snapshot_date'], r['source'], r['run_mode'])
    groups[key].append(r)

print("=" * 80, flush=True)
print("Recent recommendations:", flush=True)
print("=" * 80, flush=True)
for (date, source, mode), items in sorted(groups.items(), key=lambda x: x[0][0], reverse=True)[:10]:
    print(f"  {date} | {source} | {mode} | {len(items)} stocks", flush=True)

# Get the latest group
latest_key = sorted(groups.keys(), key=lambda x: x[0], reverse=True)[0]
latest_date, latest_source, latest_mode = latest_key
latest_stocks = groups[latest_key]

print(f"\nLatest: {latest_date} ({latest_source}, {latest_mode})", flush=True)
print(f"Found {len(latest_stocks)} stocks", flush=True)
print("=" * 80, flush=True)

# Step 2: Get current quotes for these stocks
codes = [s['ts_code'] for s in latest_stocks]
print(f"Fetching quotes for {len(codes)} stocks...", flush=True)

# Fetch quotes - need to get latest quote per stock
all_quotes = {}
for code in codes:
    resp = requests.get(
        f'{SUPABASE_URL}/rest/v1/daily_quotes',
        headers=headers,
        params={
            'ts_code': f'eq.{code}',
            'order': 'trade_date.desc',
            'limit': '1',
            'select': 'ts_code,trade_date,close',
        },
        timeout=15,
    )
    if resp.status_code == 200 and resp.json():
        all_quotes[code] = resp.json()[0]
    time.sleep(0.2)  # rate limit

print(f"Got quotes for {len(all_quotes)} stocks", flush=True)

# Step 3: Calculate and display
print(f"\n{'Code':<12} {'Name':<10} {'Rec':<8} {'Now':<8} {'Date':<12} {'Chg%':<10} Status", flush=True)
print("-" * 80, flush=True)

g = l = tc = 0
for s in sorted(latest_stocks, key=lambda x: x.get('final_score') or 0, reverse=True):
    code = s['ts_code']
    name = (s.get('stock_name') or '')[:10]
    el, eh = s.get('entry_low'), s.get('entry_high')
    rp = float((el + eh) / 2) if el and eh else (float(el) if el else (float(eh) if eh else None))
    q = all_quotes.get(code)
    
    if q and rp and rp > 0:
        now = float(q['close'])
        chg = (now - rp) / rp * 100
        cs = f"{chg:+.2f}%"
        st = "UP" if chg > 0 else ("DOWN" if chg < 0 else "FLAT")
        if chg > 0: g += 1
        elif chg < 0: l += 1
        tc += chg
    else:
        now = None; cs = "N/A"; st = "NO_DATA"
    
    print(f"{code:<12} {name:<10} {f'{rp:.2f}' if rp else 'N/A':<8} {f'{now:.2f}' if now else 'N/A':<8} {str(q['trade_date'] if q else 'N/A'):<12} {cs:<10} {st}", flush=True)

print("-" * 80, flush=True)
n = len(latest_stocks)
print(f"\nTotal: {n} | Up: {g} | Down: {l} | Avg: {tc/n:+.2f}%", flush=True)
print("\nDone.", flush=True)
