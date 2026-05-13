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
import threading
import http.server
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
    print("⚠️ 需要安装 python-telegram-bot", flush=True)
    print("  pip install python-telegram-bot", flush=True)
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
    global _processed_message_ids
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

# 日志配置 - 强制输出到 stdout
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def log_print(msg: str):
    """同时输出到 print 和 logger，确保不被缓冲"""
    print(msg, flush=True)
    logger.info(msg)


# ============================================================
# 命令处理
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📩 cmd_start 被调用, msg_id={update.message.message_id}, user={update.message.from_user.id}")
    if is_duplicate_message(update.message.message_id):
        logger.info("  ⏭ 跳过（重复消息）")
        return
    welcome = """👋 欢迎使用 OpenClaw 量化交易系统

📊 功能：
  • 自动选股推送
  • 卖出信号提醒
  • 持仓管理

输入 /help 查看可用命令"""
    await update.message.reply_text(welcome)
    logger.info(f"  ✅ 已回复 /start")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📩 cmd_help 被调用, msg_id={update.message.message_id}, user={update.message.from_user.id}")
    if is_duplicate_message(update.message.message_id):
        logger.info("  ⏭ 跳过（重复消息）")
        return
    reply = handle_command("help")
    logger.info(f"  回复内容: {reply[:50]}...")
    await update.message.reply_text(reply)
    logger.info(f"  ✅ 已回复 /help")


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_duplicate_message(update.message.message_id):
        return
    reply = format_positions()
    await update.message.reply_text(reply)
    logger.info(f"📩 收到 /positions 来自 {update.message.from_user.id}")


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_duplicate_message(update.message.message_id):
        return
    args = " ".join(context.args) if context.args else ""
    reply = handle_command("add", args)
    await update.message.reply_text(reply)
    logger.info(f"📩 收到 /add {args} 来自 {update.message.from_user.id}")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_duplicate_message(update.message.message_id):
        return
    args = " ".join(context.args) if context.args else ""
    reply = handle_command("remove", args)
    await update.message.reply_text(reply)
    logger.info(f"📩 收到 /remove {args} 来自 {update.message.from_user.id}")


async def cmd_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_duplicate_message(update.message.message_id):
        return
    reply = handle_command("import")
    await update.message.reply_text(reply)
    logger.info(f"📩 收到 /import 来自 {update.message.from_user.id}")


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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """全局错误处理——捕获所有未处理的异常"""
    logger.error(f"❌ 全局异常: {context.error}", exc_info=context.error)
    print(f"❌ ERROR: {context.error}", flush=True)


# ============================================================
# 主程序
# ============================================================
def main():
    if not BOT_TOKEN:
        print("❌ 请设置 TELEGRAM_BOT_TOKEN 环境变量", flush=True)
        sys.exit(1)

    print("=" * 60, flush=True)
    print("🤖 启动 Telegram Bot...", flush=True)
    print(f"Bot Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-4:]}", flush=True)

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
    application.add_error_handler(error_handler)

    # 检查运行模式
    use_polling = os.environ.get("TELEGRAM_POLLING", "1") == "1"
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    port = int(os.environ.get("PORT", 0))

    print("🔍 调试信息:", flush=True)
    print(f"   PORT={port}", flush=True)
    print(f"   TELEGRAM_POLLING={use_polling}", flush=True)
    print(f"   WEBHOOK_URL={'已设置' if webhook_url else '未设置'}", flush=True)
    print(f"   PYTHONUNBUFFERED={os.environ.get('PYTHONUNBUFFERED', '未设置')}", flush=True)

    if use_polling:
        # Polling 模式（默认）
        print("✅ Bot 已启动 (polling 模式)", flush=True)
        print("📡 每 3 秒拉取一次更新", flush=True)

        # 显式删除 webhook，避免与旧实例冲突
        import time
        import urllib.request
        for retry in range(3):
            try:
                delete_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
                req = urllib.request.urlopen(urllib.request.Request(delete_url), timeout=10)
                result = req.read().decode()
                print(f"🔄 删除旧 webhook (第{retry+1}次): {result}", flush=True)
                break
            except Exception as e:
                print(f"⚠️ 删除 webhook 失败 (第{retry+1}次): {e}", flush=True)
                time.sleep(2)

        # 如果 Render 提供了 PORT，启动一个轻量 HTTP 健康检查服务器
        if port:
            class HealthHandler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"OK")
                def log_message(self, format, *args):
                    pass  # 静默 HTTP 日志

            httpd = http.server.HTTPServer(("0.0.0.0", port), HealthHandler)
            health_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            health_thread.start()
            print(f"🏥 健康检查端口已开启: {port}", flush=True)

        try:
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                poll_interval=3,
                timeout=10,
                drop_pending_updates=False,
            )
        except Exception as e:
            print(f"❌ Polling 模式异常: {e}", flush=True)
            import traceback
            traceback.print_exc()
            sys.exit(1)
    elif port and webhook_url:
        # Webhook 模式
        print(f"✅ Bot 已启动 (webhook 模式)", flush=True)
        print(f"🔌 端口: {port}", flush=True)
        print(f"📡 Webhook URL: {webhook_url}", flush=True)

        try:
            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=BOT_TOKEN,
                webhook_url=webhook_url,
                drop_pending_updates=False,
            )
        except Exception as e:
            print(f"❌ Webhook 启动失败: {e}", flush=True)
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        print("❌ 配置不完整", flush=True)
        print("  设置 TELEGRAM_POLLING=1 使用 polling 模式", flush=True)
        print("  或设置 WEBHOOK_URL + PORT 使用 webhook 模式", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()