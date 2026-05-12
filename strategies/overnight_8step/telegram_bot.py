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
from datetime import datetime
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
def main():
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

    # 检查是否使用 Webhook 模式（Render 部署）
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    port = int(os.environ.get("PORT", 0))

    if webhook_url and port:
        # Webhook 模式（适合云平台部署）
        print(f"🌐 使用 Webhook 模式: {webhook_url}")
        print(f"🔌 监听端口: {port}")
        
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,  # 使用 token 作为路径增加安全性
            webhook_url=f"{webhook_url}/{BOT_TOKEN}",
        )
    else:
        # Polling 模式（适合本地开发）
        print("✅ Bot 已启动，等待消息...")
        print("按 Ctrl+C 停止")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
