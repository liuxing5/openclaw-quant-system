"""
Telegram Bot 交互处理器
========================================
监听 Telegram 消息，处理持仓管理命令和交互按钮。

功能：
  ✓ 接收 /positions /add /remove 等命令
  ✓ 处理内联键盘按钮点击（一键买入/卖出）
  ✓ 自动回复消息

运行方式：
  python telegram_bot.py

需要安装：
  pip install python-telegram-bot
"""

import os
import sys
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from aiohttp import web

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

# 消息去重：记录最近处理过的消息ID和时间戳
_processed_message_ids = {}  # {message_id: timestamp}
_MAX_MESSAGE_CACHE = 1000  # 最多缓存1000个消息ID
_DEDUP_WINDOW_SECONDS = 5  # 5秒内相同消息ID视为重复


def is_duplicate_message(message_id: int) -> bool:
    """检查消息是否重复"""
    import time
    now = time.time()
    
    if message_id in _processed_message_ids:
        last_time = _processed_message_ids[message_id]
        if now - last_time < _DEDUP_WINDOW_SECONDS:
            return True
    
    # 更新或添加时间戳
    _processed_message_ids[message_id] = now
    
    # 清理过期记录
    if len(_processed_message_ids) > _MAX_MESSAGE_CACHE:
        cutoff = now - 60  # 清理60秒前的记录
        _processed_message_ids = {
            mid: ts for mid, ts in _processed_message_ids.items()
            if ts > cutoff
        }
    
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
    """处理 /start 命令"""
    if is_duplicate_message(update.message.message_id):
        logger.debug(f"跳过重复命令: /start")
        return
    
    welcome = """👋 欢迎使用 OpenClaw 量化交易系统

📊 功能：
  • 自动选股推送
  • 卖出信号提醒
  • 持仓管理

输入 /help 查看可用命令"""
    await update.message.reply_text(welcome)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令"""
    if is_duplicate_message(update.message.message_id):
        logger.debug(f"跳过重复命令: /help")
        return
    
    reply = handle_command("help")
    await update.message.reply_text(reply)


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /positions 命令"""
    if is_duplicate_message(update.message.message_id):
        logger.debug(f"跳过重复命令: /positions")
        return
    
    reply = format_positions()
    await update.message.reply_text(reply)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /add 命令"""
    if is_duplicate_message(update.message.message_id):
        logger.debug(f"跳过重复命令: /add")
        return
    
    args = " ".join(context.args) if context.args else ""
    reply = handle_command("add", args)
    await update.message.reply_text(reply)


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /remove 命令"""
    if is_duplicate_message(update.message.message_id):
        logger.debug(f"跳过重复命令: /remove")
        return
    
    args = " ".join(context.args) if context.args else ""
    reply = handle_command("remove", args)
    await update.message.reply_text(reply)


async def cmd_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /import 命令"""
    if is_duplicate_message(update.message.message_id):
        logger.debug(f"跳过重复命令: /import")
        return
    
    reply = handle_command("import")
    await update.message.reply_text(reply)


# ============================================================
# 内联键盘按钮回调
# ============================================================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理内联键盘按钮点击"""
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = query.message.chat_id

    # 验证是否是授权用户
    if CHAT_ID and str(chat_id) != CHAT_ID:
        await query.edit_message_text("❌ 无权操作")
        return

    if data.startswith("buy_"):
        # 格式: buy_CODE_PRICE_PATH
        parts = data.split("_")
        if len(parts) >= 4:
            code = parts[1]
            price = float(parts[2])
            path = parts[3]

            result = add_position(code, price, path)
            if result["action"] == "added":
                await query.edit_message_text(
                    f"✅ 已买入: {code}\n成本: ¥{price:.2f}\n路径: {path}"
                )
            else:
                await query.edit_message_text(
                    f" 已更新: {code}\n成本: ¥{price:.2f}\n路径: {path}"
                )

    elif data.startswith("sell_"):
        # 格式: sell_CODE
        code = data.split("_")[1]
        result = remove_position(code)
        if result["action"] == "removed":
            await query.edit_message_text(f"✅ 已卖出: {code}")
        else:
            await query.edit_message_text(f" 未找到持仓: {code}")

    elif data.startswith("view_"):
        # 查看持仓详情
        await query.edit_message_text(format_positions())


# ============================================================
# 消息处理
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理普通消息"""
    # 消息去重：检查是否已处理过此消息
    message_id = update.message.message_id
    if is_duplicate_message(message_id):
        logger.debug(f"跳过重复消息: {message_id}")
        return
    
    text = update.message.text
    if not text:
        return

    # 检查是否是命令
    if text.startswith("/"):
        parts = text[1:].split()
        command = parts[0]
        args = " ".join(parts[1:]) if len(parts) > 1 else ""
        reply = handle_command(command, args)
        await update.message.reply_text(reply)


# ============================================================
# 辅助函数：发送带按钮的消息
# ============================================================
def create_buy_keyboard(picks: list, path: str = "稳健") -> InlineKeyboardMarkup:
    """为选股结果创建买入按钮"""
    buttons = []
    for pick in picks[:5]:  # 最多5个按钮
        code = pick.get("code", "")
        price = pick.get("price", 0)
        buttons.append([
            InlineKeyboardButton(
                f"买入 {code} ¥{price:.2f}",
                callback_data=f"buy_{code}_{price}_{path}",
            )
        ])
    return InlineKeyboardMarkup(buttons)


def create_position_keyboard(positions: list) -> InlineKeyboardMarkup:
    """为持仓列表创建卖出按钮"""
    buttons = []
    for pos in positions:
        code = pos.get("code", "")
        buttons.append([
            InlineKeyboardButton(
                f"卖出 {code}",
                callback_data=f"sell_{code}",
            )
        ])
    return InlineKeyboardMarkup(buttons)


# ============================================================
# 主程序
# ============================================================
async def main():
    if not BOT_TOKEN:
        print("❌ 请设置 TELEGRAM_BOT_TOKEN 环境变量")
        sys.exit(1)

    print("🤖 启动 Telegram Bot...")
    print(f"Bot Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-4:]}")
    if CHAT_ID:
        print(f"授权 Chat ID: {CHAT_ID}")
    print()

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

    # 注册按钮回调
    application.add_handler(CallbackQueryHandler(button_callback))

    # 注册消息处理器
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Render 部署：使用 webhook 模式（更适合 serverless 环境）
    port = int(os.environ.get("PORT", 0))
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    
    # 如果在 Render 上运行但没有设置 WEBHOOK_URL，自动构建
    if port and not webhook_url:
        # 尝试从 RENDER_EXTERNAL_HOSTNAME 构建
        render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")
        if render_host:
            webhook_url = f"https://{render_host}/{BOT_TOKEN}"
            print(f"ℹ️  自动构建 Webhook URL: {webhook_url}")
        else:
            print("❌ 错误：在 Render 上运行但未设置 WEBHOOK_URL")
            print("   请在 Render 环境变量中添加 WEBHOOK_URL")
            print("   格式：https://your-service.onrender.com/YOUR_BOT_TOKEN")
            sys.exit(1)
    
    if port and webhook_url:
        # Render 生产环境：webhook 模式
        print(f"✅ Bot 已启动 (webhook 模式)")
        print(f"🔌 端口: {port}")
        print(f"📡 Webhook URL: {webhook_url}")
        
        try:
            # 先删除旧的 webhook（避免冲突）
            print("🔄 清理旧的 webhook 设置...")
            await application.bot.delete_webhook(drop_pending_updates=True)
            print("✓ 旧 webhook 已删除")
            
            # 设置新的 webhook
            await application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=Update.ALL_TYPES,
            )
            print(f"✓ Webhook 已设置")
            
            # 启动 webhook 服务器
            await application.start()
            await application.updater.start_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=BOT_TOKEN,
            )
            print(f"✓ Webhook 服务器已启动")
            print(f"📡 等待 Telegram 推送消息...")
            
            # 保持运行
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            finally:
                await application.updater.stop()
                await application.stop()
                await application.shutdown()
        except Exception as e:
            print(f"❌ Webhook 启动失败: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    elif port:
        # Render 本地测试：polling + 健康检查端点
        print(f"✅ Bot 已启动 (polling 模式 - 测试)")
        print(f"🔌 健康检查端口: {port}")
        print(f"⚠️  建议设置 WEBHOOK_URL 环境变量以使用 webhook 模式")
        
        try:
            # 先删除 webhook（避免冲突）
            print("🔄 删除 webhook，切换到 polling 模式...")
            await application.bot.delete_webhook(drop_pending_updates=True)
            print("✓ Webhook 已删除")
            
            # 创建简单的 HTTP 服务器保持端口开放
            async def health_handler(request):
                return web.Response(text="OK")
            
            app = web.Application()
            app.router.add_get("/", health_handler)
            app.router.add_get("/health", health_handler)
            
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            print(f" 健康检查: http://0.0.0.0:{port}/health")
            
            # 手动启动 polling（避免事件循环冲突）
            await application.initialize()
            await application.start()
            await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            
            # 保持运行
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            finally:
                await application.updater.stop()
                await application.stop()
                await application.shutdown()
        except Exception as e:
            print(f"❌ Polling 启动失败: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        # 本地开发：直接 polling
        print("✅ Bot 已启动，等待消息...")
        print("按 Ctrl+C 停止")
        await application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.run(main())
