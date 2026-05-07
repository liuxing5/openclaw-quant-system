"""长期运行的 Bot - 处理用户回调"""
import os
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.expanduser('~/stock-recommender/.env'))


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'), user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'), dbname=os.getenv('POSTGRES_DB'),
    )


async def callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, cand_id = q.data.split('_', 1)
    
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        UPDATE push_history SET user_action=%s, user_action_at=NOW()
        WHERE candidate_id=%s AND message_id=%s;
    """, (action, int(cand_id), q.message.message_id))
    conn.commit()
    cur.close(); conn.close()
    
    await q.edit_message_reply_markup(reply_markup=None)
    await q.message.reply_text(f"已记录: {action}")


async def status(update, ctx):
    """/status 命令"""
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM raw_signals WHERE fetch_time > NOW() - INTERVAL '1 hour';")
    n = cur.fetchone()[0]
    cur.close(); conn.close()
    await update.message.reply_text(f"过去1小时入库 {n} 条信号")


def main():
    app = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(CommandHandler('status', status))
    app.run_polling()


if __name__ == '__main__':
    main()
