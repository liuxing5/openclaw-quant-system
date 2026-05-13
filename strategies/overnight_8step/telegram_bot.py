"""
Telegram Bot 交互处理器
========================================
监听 Telegram 消息，处理持仓管理命令和交互按钮。
"""

import os
import sys
import logging
import asyncio
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        CallbackQueryHandler,
        filters,
        ContextTypes,
    )
except ImportError:
    print("⚠️ 需要安装 python-telegram-bot")
    print("  pip install python-telegram-bot")
    sys.exit(1)

from position_manager import (
    handle_command,
    format_positions,
    add_position,
    remove_position,
    get_positions,
)

# ============================================================
# 配置
# ============================================================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# 消息去重
_processed_message_ids = {}
_MAX_MESSAGE_CACHE = 1000
_DEDUP_WINDOW_SECONDS = 5


def is_duplicate_message(message_id: int) -> bool:
    """检查消息是否重复"""
    import time
    now = time.time()

    if message_id in _processed_message_ids:
        last_time = _processed_message_ids[message_id]
        if now - last_time < _DEDUP_WINDOW_SECONDS:
            return True

    _processed_message_ids[message_id] = now

    if len(_processed_message_ids) > _MAX_MESSAGE_CACHE:
        cutoff = now - 60
        _processed_message_ids = {mid: ts for mid, ts in _processed_message_ids.items() if ts > cutoff}

    return False

# 日志配置
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============================================================
# 命令处理
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_duplicate_message(update.message.message_id):
        return
    welcome = """👋 欢迎使用 OpenClaw 量化交易系统

📊 功能：
  • 自动选股推送
  • 卖出信号提醒
  • 持仓管理

输入 /help 查看可用命令"""
    await update.message.reply_text(welcome)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_duplicate_message(update.message.message_id):
        return
    reply = handle_command("help")
    await update.message.reply_text(reply)


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_duplicate_message(update.message.message_id):
        return
    reply = format_positions()
    await update.message.reply_text(reply)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_duplicate_message(update.message.message_id):
        return
    args = " ".join(context.args) if context.args else ""
    reply = handle_command("add", args)
    await update.message.reply_text(reply)


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_duplicate_message(update.message.message_id):
        return
    args = " ".join(context.args) if context.args else ""
    reply = handle_command("remove", args)
    await update.message.reply_text(reply)


async def cmd_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_duplicate_message(update.message.message_id):
        return
    reply = handle_command("import")
    await update.message.reply_text(reply)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if CHAT_ID and str(chat_id) != CHAT_ID:
        await query.edit_message_text("❌ 无权操作")
        return

    if data.startswith("buy_"):
        parts = data.split("_")
        if len(parts) >= 4:
            code = parts[1]
            price = float(parts[2])
            path = parts[3]
            result = add_position(code, price, path)
            if result["action"] == "added":
                await query.edit_message_text(f"✅ 已买入: {code}\n成本: ¥{price:.2f}\n路径: {path}")
            else:
                await query.edit_message_text(f" 已更新: {code}\n成本: ¥{price:.2f}\n路径: {path}")

    elif data.startswith("sell_"):
        code = data.split("_")[1]
        result = remove_position(code)
        if result["action"] == "removed":
            await query.edit_message_text(f"✅ 已卖出: {code}")
        else:
            await query.edit_message_text(f" 未找到持仓: {code}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_id = update.message.message_id
    if is_duplicate_message(message_id):
        return

    text = update.message.text
    if not text:
        return

    if text.startswith("/"):
        parts = text[1:].split()
        command = parts[0]
        args = " ".join(parts[1:]) if len(parts) > 1 else ""
        reply = handle_command(command, args)
        await update.message.reply_text(reply)


# ============================================================
# 主程序
# ============================================================
def main():
    if not BOT_TOKEN:
        print("❌ 请设置 TELEGRAM_BOT_TOKEN 环境变量")
        sys.exit(1)

    print("🤖 启动 Telegram Bot...")
    print(f"Bot Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-4:]}")

    # 创建应用
    application = Application.builder().token(BOT_TOKEN).build()

    # 注册命令处理器
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("positions", cmd_positions))
    application.add_handler(CommandHandler("list", cmd_positions))
    application.add_handler(CommandHandler("add", cmd_add))
    application.add_handler(CommandHandler("remove", cmd_remove))
    application.add_handler(CommandHandler("del", cmd_remove))
    application.add_handler(CommandHandler("delete", cmd_remove))
    application.add_handler(CommandHandler("import", cmd_import))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 检查运行环境
    port = int(os.environ.get("PORT", 0))
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")

    # 调试信息
    print(f"🔍 调试信息:")
    print(f"   PORT={port}")
    print(f"   WEBHOOK_URL={'已设置' if webhook_url else '未设置'}")
    print(f"   RENDER_EXTERNAL_HOSTNAME={render_host if render_host else '未设置'}")

    # 自动构建 webhook URL
    if port and not webhook_url:
        if render_host:
            webhook_url = f"https://{render_host}/{BOT_TOKEN}"
            print(f"ℹ️  自动构建 Webhook URL: {webhook_url}")
        else:
            print("❌ 错误：在 Render 上运行但无法构建 WEBHOOK_URL")
            print("   请在 Render 环境变量中添加 WEBHOOK_URL")
            print("   格式：https://your-service.onrender.com/YOUR_BOT_TOKEN")
            sys.exit(1)

    if port and webhook_url:
        # Render 生产环境：webhook 模式
        print(f"✅ Bot 已启动 (webhook 模式)")
        print(f"🔌 端口: {port}")
        print(f"📡 Webhook URL: {webhook_url}")

        # 先用 Python 删除旧 webhook（不需要 curl）
        print("🔄 用 Python 删除旧 webhook...")
        try:
            import urllib.request
            delete_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
            req = urllib.request.urlopen(urllib.request.Request(delete_url), timeout=10)
            result = req.read().decode()
            print(f"   删除结果: {result}")
        except Exception as e:
            print(f"   ⚠️ 删除旧 webhook 失败（可忽略）: {e}")

        # 使用 run_webhook（同步方式，内部自动管理事件循环）
        try:
            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=BOT_TOKEN,
                webhook_url=webhook_url,
                drop_pending_updates=False,
            )
        except Exception as e:
            print(f"❌ Webhook 启动失败: {e}")
            import traceback
            traceback.print_exc()
            print("\n💡 如果 webhook 模式一直失败，可以尝试改用 polling 模式")
            print("   在 Render 环境变量中删除 WEBHOOK_URL 即可自动切换")
            sys.exit(1)
    else:
        # 本地开发：polling 模式
        print("✅ Bot 已启动 (polling 模式)")
        print("按 Ctrl+C 停止")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()