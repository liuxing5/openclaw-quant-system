"""Telegram 推送 - 每日候选池 + 异动告警"""
import os
import asyncio
import json
from datetime import date
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Bot
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


def fmt_html(text: str) -> str:
    """HTML 转义"""
    if not text: return ''
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def build_message(c: dict) -> str:
    sources = c.get('sources') or []
    if isinstance(sources, str):
        sources = json.loads(sources)
    src_names = ', '.join(set(s.get('source','') for s in sources[:5]))
    
    entry_low = c['entry_low'] if c['entry_low'] else '—'
    entry_high = c['entry_high'] if c['entry_high'] else '—'
    stop_loss = c['stop_loss'] if c['stop_loss'] else '—'
    target_1 = c['target_1'] if c['target_1'] else '—'
    target_2 = c['target_2'] if c['target_2'] else '—'
    position_pct = (c['position_pct'] or 0) * 100
    logic_tags = ', '.join(c.get('logic_tags') or [])
    
    msg = f"""🎯 <b>候选</b> <code>{fmt_html(c['ts_code'])}</code> {fmt_html(c['stock_name'] or '')}
━━━━━━━━━━━━━━
综合分: <b>{c['final_score']:.1f}</b> / 100
共识度: {c['source_diversity']} 源 / {c['mention_count']} 次提及
LLM: {c['llm_score']:.0f}  量化: {c['quant_score']:.0f}

入场: {fmt_html(str(entry_low))} - {fmt_html(str(entry_high))}
止损: {fmt_html(str(stop_loss))}
目标: {fmt_html(str(target_1))} / {fmt_html(str(target_2))}
建议仓位: {fmt_html(f'{position_pct:.0f}')}%

逻辑: {fmt_html(logic_tags)}
源: {fmt_html(src_names)}
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
        await bot.send_message(CHAT_ID, "今日无候选股", parse_mode=ParseMode.HTML)
        return
    
    # 合并所有候选股为一条消息
    lines = [f"📊 <b>{today}</b> 候选池\n共 {len(cands)} 只\n"]
    
    for c in cands:
        c = dict(c)
        sources = c.get('sources') or []
        if isinstance(sources, str):
            sources = json.loads(sources)
        src_names = ', '.join(set(s.get('source','') for s in sources[:5]))
        
        entry_low = c['entry_low'] if c['entry_low'] else '—'
        entry_high = c['entry_high'] if c['entry_high'] else '—'
        stop_loss = c['stop_loss'] if c['stop_loss'] else '—'
        target_1 = c['target_1'] if c['target_1'] else '—'
        target_2 = c['target_2'] if c['target_2'] else '—'
        position_pct = (c['position_pct'] or 0) * 100
        logic_tags = ', '.join(c.get('logic_tags') or [])
        
        lines.append(f"🎯 <b>{fmt_html(c['stock_name'] or '')}</b> <code>{fmt_html(c['ts_code'])}</code>")
        lines.append(f"综合分: <b>{c['final_score']:.1f}</b> | LLM:{c['llm_score']:.0f} 量化:{c['quant_score']:.0f}")
        lines.append(f"入场: {fmt_html(str(entry_low))}-{fmt_html(str(entry_high))}  止损: {fmt_html(str(stop_loss))}")
        lines.append(f"目标: {fmt_html(str(target_1))}/{fmt_html(str(target_2))}  仓位: {fmt_html(f'{position_pct:.0f}')}%")
        lines.append(f"逻辑: {fmt_html(logic_tags)} | 源: {fmt_html(src_names)}")
        lines.append("")
    
    msg = '\n'.join(lines)
    
    try:
        await bot.send_message(CHAT_ID, msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"push failed: {e}")
    
    conn.commit()
    cur.close(); conn.close()


if __name__ == '__main__':
    asyncio.run(push_daily_candidates())
