from datetime import datetime
import re

filepath = r"d:\pythonProject\openclaw-quant-system\recommend\选股记录汇总.txt"

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

weekday_map = {"Monday":"周一","Tuesday":"周二","Wednesday":"周三","Thursday":"周四","Friday":"周五","Saturday":"周六","Sunday":"周日"}

def fix_date(match):
    date_str = match.group(1)
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekday_cn = weekday_map[dt.strftime("%A")]
    return f"📅 {date_str} ({weekday_cn})"

def fix_date2(match):
    date_str = match.group(1)
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekday_cn = weekday_map[dt.strftime("%A")]
    return f"{date_str} ({weekday_cn})"

# 修复 📅 2026-04-17 (14:35) 格式
content = re.sub(r"📅\s+(\d{4}-\d{2}-\d{2})\s*\([^)]+\)", fix_date, content)

# 修复 2026-04-16 (14:52) 格式（无📅前缀）
content = re.sub(r"(\d{4}-\d{2}-\d{2})\s*\([^)]+\)", fix_date2, content)

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)

print("日期修正完成！")
print()

# 验证
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

for pattern in [r"📅\s+(\d{4}-\d{2}-\d{2})\s*\(([^)]+)\)", r"(\d{4}-\d{2}-\d{2})\s*\(([^)]+)\)"]:
    matches = re.findall(pattern, content)
    for date_str, label in matches:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        weekday_cn = weekday_map[dt.strftime("%A")]
        status = "✅" if weekday_cn in label else "❌"
        print(f"{date_str} | 标注: {label} | 实际: {weekday_cn} | {status}")
