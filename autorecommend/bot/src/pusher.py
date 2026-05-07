"""Telegram 推送 - 每日候选池 + 异动告警"""
import os
import asyncio
import json
from datetime import date
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(BASE_DIR, '.env'))

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
# 代理配置（本地 WSL2 需要，GitHub Actions 不需要）
PROXY_URL = os.getenv('TELEGRAM_PROXY')


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'), user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'), dbname=os.getenv('POSTGRES_DB'),
    )


def fmt_md_v2(text: str) -> str:
    """Markdown V2 转义"""
    if not text: return ''
    for ch in r'_*[]()~`>#+-=|{}.!':
        text = text.replace(ch, f'\\{ch}')
    return text


def build_message(c: dict) -> str:
    sources = c.get('sources') or []
    if isinstance(sources, str):
        sources = json.loads(sources)
    src_names = ', '.join(set(s.get('source','') for s in sources[:5]))
    
    msg = f"""🎯 *候选* `{fmt_md_v2(c['ts_code'])}` {fmt_md_v2(c['stock_name'] or '')}
━━━━━━━━━━━━━━
综合分: *{c['final_score']:.1f}* / 100
共识度: {c['source_diversity']} 源 / {c['mention_count']} 次提及
LLM: {c['llm_score']:.0f}  量化: {c['quant_score']:.0f}

入场: {c['entry_low'] or '—'} \\- {c['entry_high'] or '—'}
止损: {c['stop_loss'] or '—'}
目标: {c['target_1'] or '—'} / {c['target_2'] or '—'}
建议仓位: {(c['position_pct'] or 0)*100:.0f}%

逻辑: {fmt_md_v2(', '.join(c.get('logic_tags') or []))}
源: {fmt_md_v2(src_names)}
━━━━━━━━━━━━━━"""
    return msg


async def push_daily_candidates():
    # 配置 request（有代理则用代理）
    request_kwargs = {}
    if PROXY_URL:
        request_kwargs['proxy'] = PROXY_URL
    request = HTTPXRequest(**request_kwargs)
    
    bot = Bot(BOT_TOKEN, request=request)
    today = date.today()
    
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT * FROM daily_candidates
        WHERE snapshot_date=%s AND selected=TRUE
        ORDER BY final_score DESC;
    """, (today,))
    cands = cur.fetchall()
    
    if not cands:
        await bot.send_message(CHAT_ID, "今日无候选股", parse_mode=None)
        return
    
    # 汇总头
    header = f"📊 *{today}* 候选池\\n共 {len(cands)} 只"
    await bot.send_message(CHAT_ID, header, parse_mode=ParseMode.MARKDOWN_V2)
    
    for c in cands:
        msg = build_message(dict(c))
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ 关注", callback_data=f"watch_{c['id']}"),
            InlineKeyboardButton("❌ 忽略", callback_data=f"ignore_{c['id']}"),
            InlineKeyboardButton("📈 已入场", callback_data=f"entered_{c['id']}"),
        ]])
        try:
            sent = await bot.send_message(
                CHAT_ID, msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
            cur.execute("""
                INSERT INTO push_history (candidate_id, push_type, chat_id, message_id)
                VALUES (%s, 'pre_open', %s, %s);
            """, (c['id'], CHAT_ID, sent.message_id))
        except Exception as e:
            logger.error(f"push failed for {c['ts_code']}: {e}")
        await asyncio.sleep(1)
    
    conn.commit()
    cur.close(); conn.close()


if __name__ == '__main__':
    asyncio.run(push_daily_candidates())
