import re
import requests
import time

filepath = r"d:\pythonProject\openclaw-quant-system\recommend\选股记录汇总.txt"

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# 解析所有日期区块
date_pattern = r"(?:📅\s*)?(\d{4}-\d{2}-\d{2})\s*\([^)]+\)"
date_matches = list(re.finditer(date_pattern, content))

def fetch_real_prices(codes):
    """获取真实收盘价和涨跌幅"""
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
        print(f"  获取数据失败: {e}")
        return {}

# 按日期分组收集所有股票代码
from collections import defaultdict
date_codes = defaultdict(list)

for i, date_match in enumerate(date_matches):
    date_str = date_match.group(1)
    start_pos = date_match.end()
    end_pos = date_matches[i + 1].start() if i + 1 < len(date_matches) else len(content)
    block = content[start_pos:end_pos]
    
    codes = re.findall(r'(?:sh\.|sz\.)?(\d{6})', block)
    unique_codes = list(set(codes))
    date_codes[date_str] = unique_codes

# 批量获取所有日期的真实数据
print("正在获取真实行情数据...")
all_real_data = {}
for date, codes in date_codes.items():
    real_data = fetch_real_prices(codes)
    all_real_data[date] = real_data
    print(f"  {date}: 获取 {len(real_data)} 只股票数据")
    time.sleep(0.3)

# 逐行修正
def fix_line(line, real_data):
    """修正一行中的价格和涨幅"""
    # 尝试提取股票代码
    code_match = re.search(r'(?:sh\.|sz\.)?(\d{6})', line)
    if not code_match:
        return line
    
    code = code_match.group(1)
    if code not in real_data:
        return line
    
    new_price = f"{real_data[code]['close']:.2f}"
    new_change = f"{real_data[code]['change']:.2f}"
    
    # V1格式: 股票: sh.600118 | 现价: 84.39 | 涨幅: 4.83% | 评分: 7
    m = re.match(r'(股票:\s*(?:sh\.|sz\.)?\d{6}\s*\|\s*现价:\s*)[\d.]+(\s*\|\s*涨幅:\s*)[\d.]+(%\s*\|\s*评分:\s*\d+.*)', line)
    if m:
        return f"{m.group(1)}{new_price}{m.group(2)}{new_change}{m.group(3)}"
    
    # V3格式: 代码: sh.600707  价格: 7.19     涨幅: 5.2709%  量比:  1.88  得分: 100
    m = re.match(r'(代码:\s*(?:sh\.|sz\.)?\d{6}\s+价格:\s*)[\d.]+(\s+涨幅:\s*)[\d.]+(%\s+量比:\s*[\d.]+\s+得分:\s*\d+.*)', line)
    if m:
        return f"{m.group(1)}{new_price}{m.group(2)}{real_data[code]['change']:.4f}{m.group(3)}"
    
    # V9格式: 688525 佰维存储 分数:75 仓位:10% 理由:xxx 价格:285.2
    m = re.match(r'(\d{6}\s+\S+\s+分数:\d+\s+仓位:\d+%\s+理由:[^\s]+\s+价格:)[\d.]+', line)
    if m:
        return f"{m.group(1)}{real_data[code]['close']:.1f}"
    
    # zuiyou格式: sh.600977   hs300+zz500  14.91    5.00   3.31    5.00     0    6.14   120  特征
    m = re.match(r'((?:sh\.|sz\.)?\d{6}\s+\S+\s+)[\d.]+(\s+)[\d.]+(\s+.+)', line)
    if m:
        return f"{m.group(1)}{new_price}{m.group(2)}{new_change}{m.group(3)}"
    
    # V2/V4/V6/V7格式: sh.688027   619.14   7.12    1.70     特征描述
    # 或者: sz.300735  23.95 7.64% 3.05  3.04%    60  特征
    m = re.match(r'((?:sh\.|sz\.)?\d{6}\s+)[\d.]+(\s+)[\d.]+%?(\s+.+)', line)
    if m:
        return f"{m.group(1)}{new_price}{m.group(2)}{new_change}{m.group(3)}"
    
    return line

# 逐区块修正
new_content_parts = []
last_end = 0
total_fixed = 0

for i, date_match in enumerate(date_matches):
    date_str = date_match.group(1)
    start_pos = date_match.end()
    end_pos = date_matches[i + 1].start() if i + 1 < len(date_matches) else len(content)
    
    new_content_parts.append(content[last_end:date_match.start()])
    new_content_parts.append(content[date_match.start():start_pos])
    
    block = content[start_pos:end_pos]
    real_data = all_real_data.get(date_str, {})
    
    lines = block.split('\n')
    fixed_lines = []
    for line in lines:
        original = line
        fixed = fix_line(line, real_data)
        if fixed != original:
            total_fixed += 1
        fixed_lines.append(fixed)
    
    new_content_parts.append('\n'.join(fixed_lines))
    last_end = end_pos

new_content_parts.append(content[last_end:])
new_content = ''.join(new_content_parts)

# 保存
output_path = r"d:\pythonProject\openclaw-quant-system\recommend\选股记录汇总.txt"
with open(output_path, "w", encoding="utf-8") as f:
    f.write(new_content)

print(f"\n修正完成！共修正 {total_fixed} 行数据")
print(f"已保存至: {output_path}")
