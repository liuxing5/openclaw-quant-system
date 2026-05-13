"""
5策略共振 + LLM多源 + 八步法 Telegram 通知
=============================================
从 CSV 结果文件读取最新结果，发送摘要到 Telegram。
"""
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'strategies', 'overnight_8step'))

try:
    from notifyTelegram import send_message
except ImportError:
    print("⚠️ 无法导入 notifyTelegram，跳过 Telegram 推送")
    sys.exit(0)


def find_latest_csv() -> Path:
    results_dir = Path('results').resolve()
    if not results_dir.exists():
        return None
    files = sorted(results_dir.glob('resonance_*.csv'), reverse=True)
    return files[0] if files else None


def build_message(csv_path: Path) -> str:
    candidates = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            candidates.append(row)

    date_str = csv_path.stem.replace('resonance_', '')

    msg = f"🎯 5策略共振+LLM+八步法 — {date_str}\n\n"

    if not candidates:
        msg += "📭 今日无标的通过三层筛选\n"
        msg += "  共振 → LLM多源 → 八步法 无交集\n"
        return msg

    total = len(candidates)
    core_pass = sum(1 for c in candidates if c.get('ma_20week') == 'True'
                    and c.get('ma_bullish') == 'True'
                    and c.get('macd') == 'True')
    annual_pass = sum(1 for c in candidates if c.get('annual_line') == 'True')
    boll_pass = sum(1 for c in candidates if c.get('bollinger') == 'True')

    msg += "📊 三层筛选结果\n"
    msg += f"  入选标的: {total} 只\n"
    msg += f"  核心3策略全过: {core_pass}/{total}\n"
    msg += f"  年线通过: {annual_pass}/{total}\n"
    msg += f"  布林上轨通过: {boll_pass}/{total}\n\n"

    top_n = min(total, 5)
    msg += f"🏆 精选推荐 ({top_n}只)\n"
    for i, c in enumerate(candidates[:top_n]):
        code = c.get('code', '')
        name = c.get('name', '')
        pct = float(c.get('pct', 0))
        score = float(c.get('score', 0))
        passed = int(c.get('passed_count', 0))
        total_c = int(c.get('total_count', 0))
        tags = c.get('tags', '')

        msg += f"  {i+1}. {code} {name}  +{pct:.2f}%  评{score:.0f}  [{passed}/{total_c}]"
        if tags:
            msg += f"  {tags}"
        msg += "\n"

    msg += "\n💡 止损铁律\n"
    msg += "  稳健路径：次日09:35未维持昨收+1%，直接出局\n"
    msg += "  高位路径：次日竞价弱于昨收，集合竞价结束即清仓\n"
    msg += "  全局止损：亏损超2.5%无条件止损\n"
    msg += f"\n📄 结果文件: results/{csv_path.name}"

    return msg


def main():
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')

    if not bot_token or not chat_id:
        print("⚠️ TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未设置，跳过推送")
        return

    os.environ['TELEGRAM_BOT_TOKEN'] = bot_token
    os.environ['TELEGRAM_CHAT_ID'] = chat_id

    csv_path = find_latest_csv()
    if not csv_path:
        print("⚠️ 未找到 resonance_*.csv 结果文件，跳过推送")
        return

    msg = build_message(csv_path)
    print(f"发送共振策略报告 ({len(msg)} 字符)...")
    success = send_message(msg)

    if success:
        print("✅ Telegram 推送成功")
    else:
        print("❌ Telegram 推送失败")
        sys.exit(1)


if __name__ == '__main__':
    main()
