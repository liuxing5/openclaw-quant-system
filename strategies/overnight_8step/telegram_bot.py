"""
Telegram Bot 交互处理器 - 诊断版
========================================
- 文件锁防止同机多实例 polling
- 遇 Conflict 自动退避重试
- 健康检查 HTTP 服务（Render 兼容）
"""

import os, sys, logging, threading, http.server, time, uuid, asyncio, json, random

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

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
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot.lock")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _p(msg):
    print(msg, flush=True)

# ============================================================
# PID 文件锁
# ============================================================
INSTANCE_ID = str(uuid.uuid4())[:8]


def acquire_lock():
    """获取文件锁，如果已有活着的实例则拒绝启动"""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                data = json.load(f)
            old_pid = data.get("pid", 0)
            if _pid_alive(old_pid):
                _p(f"❌ 已有实例运行中 (PID={old_pid})，拒绝启动")
                return False
            _p(f"🔓 旧锁已失效 (PID={old_pid} 已不存在)，覆盖")
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            pass

    lock_data = {
        "pid": os.getpid(),
        "instance": INSTANCE_ID,
        "started_at": time.time(),
    }
    with open(LOCK_FILE, "w") as f:
        json.dump(lock_data, f)
    _p(f"🔒 锁已获取 PID={os.getpid()} INSTANCE={INSTANCE_ID}")
    return True


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            _p("🔓 锁已释放")
    except OSError:
        pass


# ============================================================
# 去重：防止同一条消息被处理多次
# ============================================================
_PROCESSED_UPDATES = set()
_MAX_PROCESSED = 500


def _is_duplicate(update: Update) -> bool:
    """检查是否为重复更新"""
    if update.effective_message:
        uid = update.effective_message.message_id
        chat = update.effective_chat.id if update.effective_chat else 0
        key = (chat, uid)
        if key in _PROCESSED_UPDATES:
            return True
        _PROCESSED_UPDATES.add(key)
        if len(_PROCESSED_UPDATES) > _MAX_PROCESSED:
            # 只保留最近的一半
            items = list(_PROCESSED_UPDATES)
            _PROCESSED_UPDATES.clear()
            _PROCESSED_UPDATES.update(items[-_MAX_PROCESSED // 2:])
    return False


# ============================================================
# 调试：捕获所有更新
# ============================================================
async def debug_catch_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """捕获所有没有被其他 handler 处理的更新"""
    if _is_duplicate(update):
        return
    _p(f" [DEBUG] 未处理更新: type={type(update).__name__}")
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
    if _is_duplicate(update):
        return
    _p(f"📩 cmd_start from={update.message.from_user.id}")
    await update.message.reply_text(
        "👋 欢迎使用 OpenClaw 量化交易系统\n\n输入 /help 查看可用命令"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_duplicate(update):
        return
    _p(f"📩 cmd_help from={update.message.from_user.id}")
    reply = handle_command("help")
    await update.message.reply_text(reply)


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_duplicate(update):
        return
    _p(f"📩 cmd_positions from={update.message.from_user.id}")
    reply = format_positions()
    await update.message.reply_text(reply)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_duplicate(update):
        return
    args = " ".join(context.args) if context.args else ""
    _p(f"📩 cmd_add args='{args}' from={update.message.from_user.id}")
    reply = handle_command("add", args)
    await update.message.reply_text(reply)


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_duplicate(update):
        return
    args = " ".join(context.args) if context.args else ""
    _p(f"📩 cmd_remove args='{args}' from={update.message.from_user.id}")
    reply = handle_command("remove", args)
    await update.message.reply_text(reply)


async def cmd_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_duplicate(update):
        return
    _p(f"📩 cmd_import from={update.message.from_user.id}")
    reply = handle_command("import")
    await update.message.reply_text(reply)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_duplicate(update):
        return
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
_JITTER_RANGE = 10

def _jitter():
    return random.randint(0, _JITTER_RANGE)


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


def _conflict_cleanup():
    _p("🧹 Conflict 专项清理...")
    delete_webhook()
    delete_webhook()
    _p("⏳ 等待 30s 让旧连接超时...")
    time.sleep(30)


async def _managed_polling(app, poll_interval=3, timeout=10, drop_pending=True):
    """
    手动管理 polling 生命周期 —— 不依赖 run_polling() 的阻塞行为。
    当 updater 因任何原因停止时（Conflict、网络错误等），自动退出。
    """
    async with app:
        _p(" 初始化...")
        await app.initialize()
        
        # 关键修复：彻底清除所有待处理更新，防止重启后重复处理
        # 1. 先删除 webhook 并丢弃待处理更新
        try:
            await app.bot.delete_webhook(drop_pending_updates=True)
            _p("🔄 已删除 webhook 并丢弃待处理更新")
        except Exception as e:
            _p(f"⚠️ 删除 webhook 失败: {e}")
        
        # 2. 循环获取并跳过所有待处理更新（可能超过 100 条）
        try:
            total_skipped = 0
            while True:
                updates = await app.bot.get_updates(limit=100)
                if not updates:
                    break
                last_id = max(u.update_id for u in updates)
                total_skipped += len(updates)
                _p(f"🔄 跳过 {len(updates)} 个待处理更新 (最新 update_id={last_id})")
                # 设置 offset 跳过这批更新
                await app.bot.get_updates(offset=last_id + 1)
                # 如果返回少于 100 条，说明已经全部获取完毕
                if len(updates) < 100:
                    break
            if total_skipped > 0:
                _p(f"✅ 共跳过 {total_skipped} 个旧更新")
            else:
                _p("🔄 无待处理更新")
        except Exception as e:
            _p(f"⚠️ 跳过待处理更新失败: {e}")
        
        await app.start()
        await app.updater.start_polling(
            poll_interval=poll_interval,
            timeout=timeout,
            drop_pending_updates=drop_pending,
        )
        _p("📡 Polling 已启动，监控 updater 状态...")
        while app.updater.running:
            await asyncio.sleep(2)
        _p("⚠️ Updater 已停止")
    _p("🔒 Application 已关闭")


async def _shutdown_app(app):
    try:
        if app.running:
            await app.stop()
    except Exception:
        pass
    try:
        await app.shutdown()
    except Exception:
        pass


def _wait_with_jitter(base_seconds, label=""):
    j = _jitter()
    total = base_seconds + j
    _p(f"⏳ 等待 {total}s ({base_seconds}s + jitter {j}s) {label}...")
    time.sleep(total)


def run_polling_forever():
    """永久 polling —— Conflict 时主动退避，不管什么原因退出都重来"""
    attempt = 0
    consecutive_conflicts = 0
    drop_pending = True

    while True:
        attempt += 1
        _p(f"🚀 Polling (第{attempt}次) drop_pending={drop_pending}...")

        app = build_app()
        is_conflict = False
        try:
            asyncio.run(_managed_polling(app, drop_pending=drop_pending))
        except Exception as e:
            err_str = str(e)
            is_conflict = "Conflict" in err_str or "terminated by other" in err_str
            _p(f"❌ Polling 异常: {e}")
        else:
            _p("Polling 正常退出（不应发生）")

        try:
            asyncio.run(_shutdown_app(app))
        except Exception:
            pass

        drop_pending = True

        if is_conflict:
            consecutive_conflicts += 1
            _conflict_cleanup()
            base_wait = min(consecutive_conflicts * 30, 180)
        else:
            consecutive_conflicts = 0
            delete_webhook()
            base_wait = min(attempt * 10, 60)

        _wait_with_jitter(base_wait, f"(连续 Conflict: {consecutive_conflicts})")


def main():
    import atexit

    if not BOT_TOKEN:
        _p("❌ 缺 BOT_TOKEN"); sys.exit(1)

    _p("=" * 50)
    _p(f"🤖 OpenClaw Bot 诊断版 (instance={INSTANCE_ID})")

    if not acquire_lock():
        sys.exit(1)
    atexit.register(release_lock)

    delete_webhook()
    _wrap = "= Render 部署需要 35s+ 确保旧容器长连接在 Telegram 服务端超时"
    _wait_with_jitter(35, _wrap)

    port = int(os.environ.get("PORT", 0))
    _p(f"PORT={port}")

    if port:
        start_health_server(port)

    run_polling_forever()


if __name__ == "__main__":
    main()