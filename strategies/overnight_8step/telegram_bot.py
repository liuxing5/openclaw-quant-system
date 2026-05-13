"""
Telegram Bot 交互处理器
========================================
监听 Telegram 消息，处理持仓管理命令和交互按钮。
使用 PID 锁文件确保只有一个实例在 polling。
"""

import os
import sys
import logging
import threading
import http.server
import time
import signal
import atexit

# ⚠️ 必须在任何导入之前设置 PYTHONPATH
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

try:
    from telegram import Update
    from telegram.ext import (
        Application,
        ApplicationBuilder,
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
    handle_command, format_positions,
    add_position, remove_position, get_positions,
)

# ============================================================
# 配置
# ============================================================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
LOCK_FILE = os.path.join(os.path.dirname(__file__), ".bot_lock")

# 日志
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _p(msg):
    """打印并刷新"""
    print(msg, flush=True)


# ============================================================
# PID 锁 —— 确保只有一个实例
# ============================================================
def acquire_lock():
    """获取 PID 锁。如果旧实例仍在运行，等待其退出。"""
    for attempt in range(60):  # 最多等 60 秒
        if os.path.exists(LOCK_FILE):
            try:
                with open(LOCK_FILE, "r") as f:
                    old_pid = int(f.read().strip())
                # 检查旧进程是否还活着
                try:
                    os.kill(old_pid, 0)  # signal 0 不杀进程，只检查是否存在
                    if attempt == 0:
                        _p(f"⏳ 旧实例 (PID={old_pid}) 仍在运行，等待退出...")
                    time.sleep(1)
                    continue
                except (OSError, ProcessLookupError):
                    # 旧进程已退出，删除锁文件
                    _p(f"   旧实例 (PID={old_pid}) 已退出")
                    os.remove(LOCK_FILE)
            except (ValueError, FileNotFoundError):
                pass

        # 写入新 PID
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        _p(f"🔒 PID 锁已获取 (PID={os.getpid()})")

        # 注册退出时清理
        atexit.register(release_lock)
        return True

    _p("❌ 等待旧实例退出超时（60秒）")
    return False


def release_lock():
    """释放 PID 锁"""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


# ============================================================
# 命令处理
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 欢迎使用 OpenClaw 量化交易系统\n\n"
        "📊 功能：\n"
        "  • 自动选股推送\n"
        "  • 卖出信号提醒\n"
        "  • 持仓管理\n\n"
        "输入 /help 查看可用命令"
    )
    logger.info(f"✅ 回复 /start → {update.message.from_user.id}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = handle_command("help")
    await update.message.reply_text(reply)
    logger.info(f"✅ 回复 /help → {update.message.from_user.id}")


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = format_positions()
    await update.message.reply_text(reply)
    logger.info(f"✅ 回复 /positions → {update.message.from_user.id}")


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args) if context.args else ""
    reply = handle_command("add", args)
    await update.message.reply_text(reply)
    logger.info(f"✅ 回复 /add {args} → {update.message.from_user.id}")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args) if context.args else ""
    reply = handle_command("remove", args)
    await update.message.reply_text(reply)
    logger.info(f"✅ 回复 /remove {args} → {update.message.from_user.id}")


async def cmd_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            result = add_position(parts[1], float(parts[2]), parts[3])
            await query.edit_message_text(f"✅ 已买入: {parts[1]}\n成本: ¥{parts[2]}\n路径: {parts[3]}")
    elif data.startswith("sell_"):
        code = data.split("_")[1]
        result = remove_position(code)
        await query.edit_message_text(f"✅ 已卖出: {code}" if result["action"] == "removed" else f"未找到持仓: {code}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or not text.startswith("/"):
        return
    parts = text[1:].split()
    reply = handle_command(parts[0], " ".join(parts[1:]) if len(parts) > 1 else "")
    await update.message.reply_text(reply)


# ============================================================
# 主程序
# ============================================================
def build_app():
    """创建并配置 Application"""
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("list", cmd_positions))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("del", cmd_remove))
    app.add_handler(CommandHandler("delete", cmd_remove))
    app.add_handler(CommandHandler("import", cmd_import))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


def start_health_server(port):
    """启动轻量 HTTP 健康检查服务器"""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args):
            pass
    srv = http.server.HTTPServer(("0.0.0.0", port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _p(f"🏥 健康检查: 0.0.0.0:{port}")


def delete_webhook():
    """删除旧 webhook"""
    import urllib.request
    for i in range(3):
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
            req = urllib.request.urlopen(urllib.request.Request(url), timeout=10)
            _p(f"🔄 删除 webhook (第{i+1}次): {req.read().decode()}")
            return
        except Exception as e:
            time.sleep(2)


def run_polling_forever(app):
    """永久 polling 循环 —— 断了就重来"""
    attempt = 0
    while True:
        attempt += 1
        try:
            _p(f"🚀 Polling 启动 (第{attempt}次)...")
            app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                poll_interval=3,
                timeout=10,
                drop_pending_updates=True,
            )
            _p("Polling 正常退出")
            break
        except Exception as e:
            err = str(e)
            if "Conflict" in err or "409" in err:
                wait = attempt * 5
                _p(f"⚠️ Conflict (第{attempt}次)，等{wait}秒后重试...")
                time.sleep(wait)
                # 重建 application（内部状态可能已损坏）
                app = build_app()
                delete_webhook()
            else:
                _p(f"❌ Polling 异常: {e}")


def main():
    if not BOT_TOKEN:
        _p("❌ 请设置 TELEGRAM_BOT_TOKEN")
        sys.exit(1)

    _p("=" * 50)
    _p("🤖 OpenClaw Telegram Bot")

    # PID 锁 —— 确保只有一个实例
    if not acquire_lock():
        sys.exit(1)

    # 环境检查
    port = int(os.environ.get("PORT", 0))
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    _p(f"PORT={port}  POLLING={os.environ.get('TELEGRAM_POLLING','1')}")

    # 删除旧 webhook
    delete_webhook()

    # 健康检查
    if port:
        start_health_server(port)

    # 创建 app 并运行
    app = build_app()
    run_polling_forever(app)


if __name__ == "__main__":
    main()
