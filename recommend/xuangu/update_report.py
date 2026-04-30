"""
完整报告更新工具
用法: python update_report.py
功能: 运行zuiyou.py并将结果追加到完整报告.md
"""
import os
import subprocess
from datetime import datetime

REPORT_PATH = r"d:\pythonProject\openclaw-quant-system\recommend\完整报告.md"
ZUIYOU_PATH = r"d:\pythonProject\openclaw-quant-system\recommend\xuangu\zuiyou1.py"

def run_zuiyou():
    """运行zuiyou.py并返回输出"""
    result = subprocess.run(
        ["python", ZUIYOU_PATH],
        capture_output=True,
        text=True,
        encoding='utf-8',
        cwd=os.path.dirname(ZUIYOU_PATH),
        timeout=300
    )
    return result.stdout.strip()

def parse_output(output):
    """解析zuiyou输出"""
    lines = output.split('\n')
    sections = []
    current_section = None
    current_stocks = []
    market_sentiment = ""
    
    for line in lines:
        line = line.strip()
        
        # 提取市场情绪
        if '市场情绪' in line:
            market_sentiment = line
        
        # 识别新段落
        if '稳健路径' in line or '高位路径' in line:
            if current_section and current_stocks:
                sections.append((current_section, current_stocks))
            current_section = line
            current_stocks = []
        # 识别股票行（以sh.或sz.开头）
        elif line.startswith('sh.') or line.startswith('sz.'):
            parts = line.split()
            if len(parts) >= 9:
                current_stocks.append({
                    'code': parts[0],
                    'pool': parts[1],
                    'price': parts[2],
                    'pct': parts[3],
                    'vol_ratio': parts[4],
                    'turn': parts[5],
                    'streak': parts[6],
                    'bias': parts[7],
                    'score': parts[8],
                    'features': ' '.join(parts[9:]) if len(parts) > 9 else ''
                })
    
    if current_section and current_stocks:
        sections.append((current_section, current_stocks))
    
    return market_sentiment, sections

def format_daily_md(date, market_sentiment, sections, raw_output):
    """格式化为Markdown"""
    md = f"### 【{date}】\n\n"
    
    if market_sentiment:
        md += f"**{market_sentiment}**\n\n"
    
    if not sections:
        md += "**结果**: 今日暂无符合条件的标的\n\n"
        md += "---\n\n"
        return md
    
    for section_header, stocks in sections:
        md += f"**{section_header}**\n\n"
        md += "| 代码 | 池子 | 价格 | 涨幅% | 量比 | 换手% | 连板 | 乖离% | 得分 | 特征 |\n"
        md += "|------|------|------|-------|------|-------|------|-------|------|------|\n"
        
        for s in stocks:
            md += f"| {s['code']} | {s['pool']} | {s['price']} | {s['pct']} | {s['vol_ratio']} | {s['turn']} | {s['streak']} | {s['bias']} | {s['score']} | {s['features']} |\n"
        
        md += "\n"
    
    md += "---\n\n"
    return md

def update_report():
    """更新报告"""
    date = datetime.now().strftime("%Y-%m-%d")
    
    print(f"📊 更新每日选股报告 - {date}")
    print("=" * 50)
    
    # 1. 运行zuiyou.py
    print("🔍 运行zuiyou.py获取最新选股结果...")
    try:
        output = run_zuiyou()
    except Exception as e:
        print(f"❌ 运行zuiyou.py失败: {e}")
        return
    
    if not output:
        print("⚠️ zuiyou.py未输出任何内容")
        return
    
    print("✅ zuiyou.py运行成功")
    print("\n" + output[:500] + "...\n")
    
    # 2. 解析输出
    market_sentiment, sections = parse_output(output)
    
    # 3. 格式化
    daily_md = format_daily_md(date, market_sentiment, sections, output)
    
    # 4. 更新报告
    with open(REPORT_PATH, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 查找每日选股日志标记
    marker = "<!-- DAILY_LOG_START -->"
    end_marker = "<!-- DAILY_LOG_END -->"
    
    if marker in content:
        # 在标记后插入新内容
        insert_pos = content.index(marker) + len(marker)
        new_content = content[:insert_pos] + "\n\n" + daily_md + content[insert_pos:]
    else:
        # 如果没有标记，在文件末尾添加
        new_content = content + f"\n\n{marker}\n\n{daily_md}\n{end_marker}\n"
    
    # 更新最后更新日期
    if "**最后更新**:" in new_content:
        new_content = new_content.replace(
            "**最后更新**: " + (datetime.now().replace(day=1).strftime("%Y-%m-%d")),
            f"**最后更新**: {date}"
        )
    else:
        # 在生成时间后添加最后更新
        new_content = new_content.replace(
            "**生成时间**:",
            f"**最后更新**: {date}  \n**生成时间**:"
        )
    
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"✅ 报告已更新: {date}")
    print(f"📁 文件: {REPORT_PATH}")
    print("\n📋 今日选股结果:")
    print(daily_md)

if __name__ == "__main__":
    update_report()
