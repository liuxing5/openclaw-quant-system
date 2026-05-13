"""
Telegram Bot 交互处理器 - 诊断版
========================================
使用 PID 锁文件确保只有一个实例在 polling。
"""

import os, sys, logging, threading, http.server, time, atexit

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

try:
    from telegram import Update
    from telegram.ext import (
        Application, ApplicationBuilder,
        CommandHandler, MessageHandler, CallbackQueryHandler,
        filters, ContextTypes,
    )
except ImportError:
    print("pip install python-telegram-bot", flush=True)
    sys.exit(1)

from position_manager import (
    handle_command, format_positions,
    add_position, remove_position, get_positions,
)

# ============================================================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
LOCK_FILE = os.path.join(os.path.dirname(__file__), ".bot_lock")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _p(msg):
    print(msg, flush=True)


# ============================================================
# PID 锁
# ============================================================
def acquire_lock():
    for attempt in range(60):
        if os.path.exists(LOCK_FILE):
            try:
                with open(LOCK_FILE, "r") as f:
                    old_pid = int(f.read().strip())
                try:
                    os.kill(old_pid, 0)
                    if attempt == 0:
                        _p(f"⏳ 旧实例 PID={old_pid} 仍在运行，等待...")
                    time.sleep(1)
                    continue
                except (OSError, ProcessLookupError):
                    _p(f"   旧实例 PID={old_pid} 已退出")
                    os.remove(LOCK_FILE)
            except (ValueError, FileNotFoundError):
                pass
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        _p(f"🔒 锁已获取 PID={os.getpid()}")
        atexit.register(release_lock)
        return True
    _p("❌ 等待超时")
    return False


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


# ============================================================
# 调试：捕获所有更新
# ============================================================
async def debug_catch_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """捕获所有没有被其他 handler 处理的更新"""
    _p(f"🔍 [DEBUG] 未处理更新: type={type(update).__name__}")
    if update.message:
        msg = update.message
        _p(f"   text='{msg.text}' from={msg.from_user.id} chat={msg.chat_id}")
        if msg.entities:
            for e in msg.entities:
                _p(f"   entity: type={e.type} offset={e.offset} len={e.length}")


# ============================================================
# 命令处理
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _p(f"📩 cmd_start from={update.message.from_user.id}")
    await update.message.reply_text(
        "👋 欢迎使用 OpenClaw 量化交易系统\n\n输入 /help 查看可用命令"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _p(f"📩 cmd_help from={update.message.from_user.id}")
    reply = handle_command("help")
    await update.message.reply_text(reply)


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _p(f"📩 cmd_positions from={update.message.from_user.id}")
    reply = format_positions()
    await update.message.reply_text(reply)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args) if context.args else ""
    _p(f"📩 cmd_add args='{args}' from={update.message.from_user.id}")
    reply = handle_command("add", args)
    await update.message.reply_text(reply)


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = " ".join(context.args) if context.args else ""
    _p(f"📩 cmd_remove args='{args}' from={update.message.from_user.id}")
    reply = handle_command("remove", args)
    await update.message.reply_text(reply)


async def cmd_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _p(f"📩 cmd_import from={update.message.from_user.id}")
    reply = handle_command("import")
    await update.message.reply_text(reply)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if CHAT_ID and str(query.message.chat_id) != CHAT_ID:
        await query.edit_message_text("❌ 无权操作")
        return
    if data.startswith("buy_"):
        parts = data.split("_")
        if len(parts) >= 4:
            add_position(parts[1], float(parts[2]), parts[3])
            await query.edit_message_text(f"✅ 已买入: {parts[1]}")
    elif data.startswith("sell_"):
        code = data.split("_")[1]
        remove_position(code)
        await query.edit_message_text(f"✅ 已卖出: {code}")


# ============================================================
# 构建应用
# ============================================================
def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # 错误处理
    async def _err(update: object, context: ContextTypes.DEFAULT_TYPE):
        _p(f"❌ ERROR: {context.error}")
        logger.error(f"Handler error: {context.error}", exc_info=context.error)
    app.add_error_handler(_err)

    # 命令处理器
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

    # 兜底：捕获所有未被处理的更新（放在最后）
    app.add_handler(MessageHandler(filters.ALL, debug_catch_all))

    return app


# ============================================================
# 基础设施
# ============================================================
def start_health_server(port):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _p(f"🏥 健康检查: 0.0.0.0:{port}")


def delete_webhook():
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
    attempt = 0
    while True:
        attempt += 1
        try:
            _p(f"🚀 Polling (第{attempt}次)...")
            app.run_polling(
                poll_interval=3,
                timeout=10,
                drop_pending_updates=False,
            )
            _p("Polling 正常退出"); break
        except Exception as e:
            err = str(e)
            if "Conflict" in err or "409" in err:
                wait = attempt * 5
                _p(f"⚠️ Conflict，等{wait}s重试...")
                time.sleep(wait)
                app = build_app()
                delete_webhook()
            else:
                _p(f"❌ Polling 异常: {e}")


def main():
    if not BOT_TOKEN:
        _p("❌ 缺 BOT_TOKEN"); sys.exit(1)

    _p("=" * 50)
    _p("🤖 OpenClaw Bot 诊断版")

    if not acquire_lock():
        sys.exit(1)

    port = int(os.environ.get("PORT", 0))
    _p(f"PORT={port}")

    delete_webhook()

    if port:
        start_health_server(port)

    app = build_app()
    run_polling_forever(app)


if __name__ == "__main__":
    main()