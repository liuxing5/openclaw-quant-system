import re
import requests
import time

filepath = r"d:\pythonProject\openclaw-quant-system\recommend\选股记录汇总.txt"

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# 解析所有日期区块
date_pattern = r"(?:📅\s*)?(\d{4}-\d{2}-\d{2})\s*\([^)]+\)"
date_matches = list(re.finditer(date_pattern, content))

records = []
for i, date_match in enumerate(date_matches):
    date_str = date_match.group(1)
    start_pos = date_match.end()
    end_pos = date_matches[i + 1].start() if i + 1 < len(date_matches) else len(content)
    block = content[start_pos:end_pos]
    
    # 提取所有股票代码和价格
    patterns = [
        r'(?:sh\.|sz\.)?(\d{6})\s+(?:hs300\+zz500|zz1000|hs300|zz500)?\s+([\d.]+)\s+([\d.]+)',  # zuiyou格式
        r'股票:\s*(?:sh\.|sz\.)?(\d{6})\s*\|\s*现价:\s*([\d.]+)\s*\|\s*涨幅:\s*([\d.]+)%',  # V1格式
        r'代码[:\s]*(?:sh\.|sz\.)?(\d{6})\s+价格[:\s]*([\d.]+)\s+涨幅[:\s]*([\d.]+)',  # V3格式
        r'(?:sh\.|sz\.)?(\d{6})\s+([\d.]+)\s+([\d.]+)%',  # V2/V4/V5/V6/V7格式
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, block)
        for code, price, change in matches:
            try:
                price_val = float(price)
                change_val = float(change)
                if price_val > 0:
                    records.append({
                        "date": date_str,
                        "code": code,
                        "price": price_val,
                        "change": change_val,
                        "line": f"{code} {price} {change}"
                    })
            except ValueError:
                continue

# 去重
seen = set()
unique_records = []
for r in records:
    key = (r['date'], r['code'])
    if key not in seen:
        seen.add(key)
        unique_records.append(r)

print(f"共找到 {len(unique_records)} 条记录需要验证\n")

# 批量查询腾讯API
def fetch_real_prices(codes, date):
    """获取指定日期的真实收盘价和涨跌幅"""
    symbols = ','.join([f"sh{c}" if c.startswith('6') else f"sz{c}" for c in codes])
    url = f"https://qt.gtimg.cn/q={symbols}"
    try:
        resp = requests.get(url, timeout=10)
        resp.encoding = 'gbk'
        lines = resp.text.strip().split(';')
        results = {}
        for line in lines:
            if '~' in line:
                parts = line.split('~')
                if len(parts) > 5:
                    code = parts[2]
                    try:
                        close = float(parts[3])
                        prev_close = float(parts[4])
                        change_pct = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0
                        results[code] = {"close": close, "change": round(change_pct, 2)}
                    except (ValueError, IndexError):
                        pass
        return results
    except Exception as e:
        print(f"  获取 {date} 数据失败: {e}")
        return {}

# 按日期分组验证
from collections import defaultdict
by_date = defaultdict(list)
for r in unique_records:
    by_date[r['date']].append(r)

errors = []
corrections = []

for date in sorted(by_date.keys()):
    recs = by_date[date]
    codes = [r['code'] for r in recs]
    
    real_data = fetch_real_prices(codes, date)
    time.sleep(0.3)
    
    if not real_data:
        print(f"⚠️ {date}: 无法获取数据")
        continue
    
    for r in recs:
        code = r['code']
        if code in real_data:
            real_close = real_data[code]['close']
            real_change = real_data[code]['change']
            
            price_diff = abs(r['price'] - real_close)
            change_diff = abs(r['change'] - real_change)
            
            if price_diff > 0.02 or change_diff > 0.5:
                errors.append({
                    "date": date,
                    "code": code,
                    "recorded_price": r['price'],
                    "real_price": real_close,
                    "recorded_change": r['change'],
                    "real_change": real_change,
                })
                corrections.append({
                    "date": date,
                    "code": code,
                    "old_price": r['price'],
                    "new_price": real_close,
                    "old_change": r['change'],
                    "new_change": real_change,
                })
                print(f"❌ {date} | {code} | 记录价:{r['price']:.2f} 实际价:{real_close:.2f} | 记录涨幅:{r['change']:.2f}% 实际涨幅:{real_change:.2f}%")
            else:
                print(f"✅ {date} | {code} | {r['price']:.2f} ({r['change']:.2f}%)")
        else:
            print(f"⚠️ {date} | {code} | 未找到数据")

print(f"\n{'='*60}")
print(f"验证完成！共 {len(unique_records)} 条记录")
print(f"正确: {len(unique_records) - len(errors)} 条")
print(f"错误: {len(errors)} 条")

if corrections:
    print(f"\n需要修正的数据:")
    for c in corrections:
        print(f"  {c['date']} | {c['code']}: 价格 {c['old_price']:.2f}→{c['new_price']:.2f}, 涨幅 {c['old_change']:.2f}%→{c['new_change']:.2f}%")
