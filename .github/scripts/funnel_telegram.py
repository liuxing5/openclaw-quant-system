"""
漏斗策略 Telegram 通知
========================
从 funnel_results 表读取最新结果，发送摘要到 Telegram。
复用 overnight_8step 的 notifyTelegram 模块。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'strategies', 'overnight_8step'))

try:
    from notifyTelegram import send_message
except ImportError:
    print("⚠️ 无法导入 notifyTelegram，跳过 Telegram 推送")
    sys.exit(0)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.db.connection import get_db
from psycopg2.extras import RealDictCursor


def build_message() -> str:
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT * FROM funnel_results ORDER BY trade_date DESC LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return None

    candidates = row['candidates']
    if isinstance(candidates, str):
        candidates = json.loads(candidates)
    candidates = candidates or []

    date_str = str(row['trade_date'])

    # 仓位状态
    if row['layer0_pass']:
        position_emoji = "✅"
        position_text = f"满仓({int(row.get('layer0_max_position', 1) * 100)}%)"
    else:
        position_emoji = "⚠️"
        position_text = f"半仓({int(row.get('layer0_max_position', 0.5) * 100)}%)"

    msg = f"🎯 七步漏斗选股 — {date_str}\n\n"

    # 市场环境
    msg += "📊 市场环境\n"
    msg += f"  上涨 {row['market_advancers']}  |  下跌 {row['market_decliners']}\n"
    msg += f"  指数 {row['market_index_close']}  |  20EMA {row['market_index_ema']}\n"
    msg += f"  {position_emoji} 仓位: {position_text}\n\n"

    # 漏斗过滤过程
    msg += "🔄 漏斗过滤\n"
    l0 = row['total_stocks']
    l1 = row['layer1_pass']
    l2 = row['layer2_pass']
    l3 = row['layer3_pass']
    l4 = row['layer4_pass']
    l5 = row['layer5_pass']
    l6 = row['layer6_pass']

    msg += f"  {l0} → L1防雷 {l1} → L2流动 {l2} → L3趋势 {l3}\n"
    msg += f"  → L4动能 {l4} → L5人气 {l5} → L6风控 {l6}\n\n"

    # 推荐标的
    if candidates:
        top_n = min(len(candidates), 5)
        msg += f"🎯 推荐标的 ({top_n}只)\n"
        for i, c in enumerate(candidates[:top_n]):
            code = c.get('ts_code', '')
            score = c.get('score', 0)
            entry = c.get('entry_price', 0)
            stop = c.get('stop_loss', 0)
            target = c.get('target_price', 0)
            plr = c.get('profit_loss_ratio', 0)
            signal = c.get('signal_type', '')
            signal_short = {'demand_absorption': '需求吸收', 'strong_relay': '强势接力'}.get(signal, signal)

            msg += f"  {i+1}. {code} 评{score} 入{entry:.2f} 止{stop:.2f} 目{target:.2f} {plr:.1f}:1"
            if signal_short:
                msg += f" [{signal_short}]"
            msg += "\n"
    else:
        msg += "📭 今日无推荐标的\n"

    # 耗时 + 报告链接
    msg += f"\n⏱ 总耗时 {row['elapsed_seconds']:.1f}s"
    msg += f"\n📄 完整报告: https://liuxing5.github.io/openclaw-quant-system/funnel/"

    return msg


def main():
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')

    if not bot_token or not chat_id:
        print("⚠️ TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未设置，跳过推送")
        return

    os.environ['TELEGRAM_BOT_TOKEN'] = bot_token
    os.environ['TELEGRAM_CHAT_ID'] = chat_id

    msg = build_message()
    if not msg:
        print("⚠️ 数据库中无漏斗策略数据，跳过推送")
        return

    print(f"发送漏斗策略报告 ({len(msg)} 字符)...")
    success = send_message(msg)

    if success:
        print("✅ Telegram 推送成功")
    else:
        print("❌ Telegram 推送失败")
        sys.exit(1)


if __name__ == '__main__':
    main()
