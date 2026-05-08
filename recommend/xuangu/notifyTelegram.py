"""
Telegram 推送模块（通用）
========================================
所有需要推送到手机的脚本都可以 import 这个模块。

用法：
    from notify_telegram import send_message, send_long_message

    send_message("选股结果: ...")
    send_long_message(very_long_text)  # 自动分段,超过4000字符也能发

环境变量配置（推荐方式,避免token写在代码里）:
    export TELEGRAM_BOT_TOKEN="7234567890:AAHdqTcvSh3xxxxxxxxxxxxx"
    export TELEGRAM_CHAT_ID="123456789"

或者直接修改下面的 DEFAULT_TOKEN 和 DEFAULT_CHAT_ID
"""

import os
import requests
import time
from typing import Optional
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# ============================================================
# 配置（优先用环境变量，fallback 到下面的默认值）
# ============================================================
DEFAULT_TOKEN = ""    # 这里填你的 token (或者用环境变量)
DEFAULT_CHAT_ID = ""  # 这里填你的 chat_id (或者用环境变量)

# Telegram 单条消息字符数上限
MAX_MSG_LENGTH = 4000


def _get_credentials() -> tuple:
    """获取 token 和 chat_id,优先从环境变量读取"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULT_TOKEN)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)

    if not token or not chat_id:
        print("⚠️ Telegram 未配置 token 或 chat_id,跳过推送")
        print("   请设置环境变量:")
        print("   export TELEGRAM_BOT_TOKEN='你的token'")
        print("   export TELEGRAM_CHAT_ID='你的chat_id'")
        return None, None

    return token, chat_id


def send_message(text: str, parse_mode: Optional[str] = None) -> bool:
    """
    发送单条消息到 Telegram。

    Args:
        text: 消息内容
        parse_mode: 可选 "Markdown" / "HTML",默认纯文本

    Returns:
        是否发送成功
    """
    token, chat_id = _get_credentials()
    if not token:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code == 200:
            return True
        else:
            print(f"⚠️ Telegram 推送失败: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"⚠️ Telegram 推送异常: {e}")
        return False


def send_long_message(text: str, parse_mode: Optional[str] = None) -> bool:
    """
    发送超长消息(自动按 4000 字符分段)。

    适用场景:zuiyou1 完整选股报告可能超过 4000 字符。
    会按"换行"边界切分,避免把一行截断。
    """
    if len(text) <= MAX_MSG_LENGTH:
        return send_message(text, parse_mode)

    # 按行切分,组装成多个 chunk
    lines = text.split("\n")
    chunks = []
    current = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 是换行符
        if current_len + line_len > MAX_MSG_LENGTH:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    print(f"  消息过长,已分 {len(chunks)} 段推送")

    all_ok = True
    for i, chunk in enumerate(chunks, 1):
        prefix = f"[{i}/{len(chunks)}]\n" if len(chunks) > 1 else ""
        ok = send_message(prefix + chunk, parse_mode)
        if not ok:
            all_ok = False
        time.sleep(0.5)  # 避免触发 Telegram 频率限制

    return all_ok


def send_stock_picks(
    title: str,
    end_d: str,
    mood_info: str,
    stable_picks: list,
    upper_picks: list,
    operation_note: str = "",
    reject_summary: str = "",
) -> bool:
    """
    格式化推送选股结果(zuiyou1 专用便捷接口)。
    每条股票信息单独推送一条消息，不折行。

    Args:
        title: 标题(如 "🔥 zuiyou1 v1.3 盘后定稿")
        end_d: 日期 (2026-04-29)
        mood_info: 情绪信息字符串
        stable_picks: 稳健路径推荐列表 [{code, price, pct, vol_ratio, turn, score, tags}, ...]
        upper_picks: 高位路径推荐列表
        operation_note: 操作建议
        reject_summary: 过滤统计摘要
    """
    # 先发送标题汇总
    header = f"🔥 {title}\n📅 {end_d}\n📊 {mood_info}"
    if not stable_picks and not upper_picks:
        header += "\n\n⚠️ 今日无符合条件的标的\n(空仓也是仓位)"
        if reject_summary:
            header += f"\n\n━━ 过滤瓶颈 ━\n{reject_summary}"
        send_message(header)
        return True

    # 发送稳健路径标题
    if stable_picks:
        send_message(f"━━ 稳健路径 ({len(stable_picks)}只) 单票≤15% ━━")
        for s in stable_picks:
            msg = f"• {s['code']} ¥{s['price']} +{s['pct']:.2f}% 量比{s.get('vol_ratio',0):.2f} 换手{s.get('turn',0):.2f}% 得分{s['score']} | {s['tags']}"
            send_message(msg)

    # 发送高位路径标题
    if upper_picks:
        send_message(f"━━ 高位路径 ({len(upper_picks)}只) 单票≤8% ━━")
        for s in upper_picks:
            msg = f"• {s['code']} ¥{s['price']} +{s['pct']:.2f}% 量比{s.get('vol_ratio',0):.2f} 换手{s.get('turn',0):.2f}% 得分{s['score']} | {s['tags']}"
            send_message(msg)

    # 发送过滤统计
    if reject_summary:
        send_message(f"━━ 过滤统计 ━━\n{reject_summary}")

    # 发送操作建议
    if operation_note:
        send_message(f"━━ 操作建议 ━━\n{operation_note}")

    return True


def send_stock_picks_with_buttons(
    title: str,
    end_d: str,
    mood_info: str,
    stable_picks: list,
    upper_picks: list,
    operation_note: str = "",
) -> bool:
    """
    发送选股结果，带内联键盘按钮（一键买入）。

    需要 python-telegram-bot 库支持。
    """
    token, chat_id = _get_credentials()
    if not token:
        return False

    # 构建消息文本
    lines = []
    lines.append(f"🔥 {title}")
    lines.append(f"📅 {end_d}")
    lines.append(f"📊 {mood_info}")
    lines.append("")

    if not stable_picks and not upper_picks:
        lines.append("⚠️ 今日无符合条件的标的")
        lines.append("(空仓也是仓位)")
        return send_message("\n".join(lines))

    if stable_picks:
        lines.append(f"━━ 稳健路径 ({len(stable_picks)} 只) ━━")
        lines.append("💰 单票≤15% 总仓位")
        for s in stable_picks:
            lines.append(f"• {s['code']}  ¥{s['price']}  +{s['pct']:.2f}%")
            lines.append(f"  得分 {s['score']} | {s['tags']}")
        lines.append("")

    if upper_picks:
        lines.append(f"━━ 高位路径 ({len(upper_picks)} 只) ━━")
        lines.append("💰 单票≤8% 总仓位")
        for s in upper_picks:
            lines.append(f"• {s['code']}  ¥{s['price']}  +{s['pct']:.2f}%")
            lines.append(f"  得分 {s['score']} | {s['tags']}")
        lines.append("")

    if operation_note:
        lines.append("━━ 操作建议 ━━")
        lines.append(operation_note)

    text = "\n".join(lines)

    # 构建内联键盘
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = []

        # 稳健路径买入按钮
        for s in stable_picks[:3]:  # 最多3个
            keyboard.append([
                InlineKeyboardButton(
                    f"🛡️ 买入 {s['code']} ¥{s['price']:.2f}",
                    callback_data=f"buy_{s['code']}_{s['price']}_稳健",
                )
            ])

        # 高位路径买入按钮
        for s in upper_picks[:3]:  # 最多3个
            keyboard.append([
                InlineKeyboardButton(
                    f"🚀 买入 {s['code']} ¥{s['price']:.2f}",
                    callback_data=f"buy_{s['code']}_{s['price']}_高位",
                )
            ])

        # 查看持仓按钮
        keyboard.append([
            InlineKeyboardButton("📊 查看持仓", callback_data="view_positions")
        ])

        markup = InlineKeyboardMarkup(keyboard)

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": markup.to_json(),
        }

        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200

    except ImportError:
        # 如果没有 telegram 库，退化为普通消息
        print("⚠️ python-telegram-bot 未安装，使用普通消息推送")
        return send_long_message(text)


def send_sell_alert(
    code: str,
    name: str,
    action: str,
    reason: str,
    profit_pct: float,
    priority: int,
) -> bool:
    """
    sell_new.py 专用:紧急卖出信号推送(优先级 ≥4 触发)。
    """
    icon = "🚨" if priority >= 5 else "⚠️"
    lines = [
        f"{icon} 紧急卖出信号",
        f"━━━━━━━━━━━━━━━━",
        f"📍 {code} {name}",
        f"📊 当前盈亏: {profit_pct:+.2f}%",
        f"🎯 动作: {action}",
        f"💡 理由: {reason}",
        f"⚡ 优先级: {priority}",
        f"━━━━━━━━━━━━━━━━",
        f"立即在 Futu NiuNiu 操作!",
    ]
    return send_message("\n".join(lines))


# ============================================================
# 自测:python notify_telegram.py 直接运行可以测试推送
# ============================================================
if __name__ == "__main__":
    import sys

    print("🧪 Telegram 推送自测")
    print("=" * 50)

    token, chat_id = _get_credentials()
    if not token:
        print("\n请先配置环境变量或修改文件顶部的 DEFAULT_TOKEN/DEFAULT_CHAT_ID")
        sys.exit(1)

    print(f"Token: {token[:10]}...{token[-4:]}")
    print(f"Chat ID: {chat_id}")
    print()

    # 测试1:简单消息
    print("测试1: 发送简单消息...")
    ok = send_message("✅ Telegram 推送配置成功!\n来自 OpenClaw 选股系统")
    print(f"  结果: {'成功' if ok else '失败'}")

    # 测试2:格式化选股推送
    print("\n测试2: 发送选股格式消息...")
    ok = send_stock_picks(
        title="zuiyou1 v1.2 测试推送",
        end_d="2026-04-29",
        mood_info="情绪: 正常 (50家涨停)",
        stable_picks=[],
        upper_picks=[
            {
                "code": "sh.600520",
                "price": 29.80,
                "pct": 3.47,
                "score": 78,
                "tags": "稳健蓄势 | 量能达标 | 换手偏高 | 首阳突破",
            }
        ],
        operation_note="单票仓位≤8%,次日竞价≤0立即清仓",
    )
    print(f"  结果: {'成功' if ok else '失败'}")

    print("\n✅ 测试完成,请检查手机 Telegram 是否收到消息")