"""
Telegram Bot 交互处理器
========================================
监听 Telegram 消息，处理持仓管理命令和交互按钮。

运行模式：
  - polling 模式（默认）：bot 主动拉取消息，适合 Render 等 PaaS 平台
  - webhook 模式：需要设置 WEBHOOK_URL，适合有固定域名的 VPS
  - 设置 TELEGRAM_POLLING=1 强制使用 polling 模式
"""

import os
import sys
import logging
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

try:
    from telegram import Update
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

    # 检查运行模式
    use_polling = os.environ.get("TELEGRAM_POLLING", "1") == "1"
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    port = int(os.environ.get("PORT", 0))

    if use_polling:
        # Polling 模式（默认，最稳定）
        print("✅ Bot 已启动 (polling 模式)")
        print("📡 每 3 秒拉取一次更新")
        print("💡 如需切换到 webhook 模式，设置 TELEGRAM_POLLING=0 并配置 WEBHOOK_URL")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            poll_interval=3,
            timeout=10,
            drop_pending_updates=False,
        )
    elif port and webhook_url:
        # Webhook 模式
        print(f"✅ Bot 已启动 (webhook 模式)")
        print(f"🔌 端口: {port}")
        print(f"📡 Webhook URL: {webhook_url}")

        # 先删除旧 webhook
        print("🔄 删除旧 webhook...")
        try:
            import urllib.request
            delete_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
            req = urllib.request.urlopen(urllib.request.Request(delete_url), timeout=10)
            result = req.read().decode()
            print(f"   删除结果: {result}")
        except Exception as e:
            print(f"   ⚠️ 删除旧 webhook 失败: {e}")

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
            print("\n💡 改用 polling 模式：在环境变量中设置 TELEGRAM_POLLING=1")
            sys.exit(1)
    else:
        print("❌ 配置不完整")
        print("  需要设置 TELEGRAM_POLLING=1 使用 polling 模式")
        print("  或设置 WEBHOOK_URL + PORT 使用 webhook 模式")
        sys.exit(1)


if __name__ == "__main__":
    main()