"""Telegram 推送 - 每日候选池 + 异动告警"""
import os
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Bot
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
PROXY_URL = os.getenv('TELEGRAM_PROXY')
RUN_MODE = os.getenv('RUN_MODE', 'morning')

# 北京时间时区
BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date():
    """获取北京时间日期（解决 GitHub Actions UTC 时区问题）"""
    return datetime.now(BEIJING_TZ).date()

MIN_SELECT_SCORE = 50


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
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


def check_market_state(cur, today):
    """市场极弱时不推荐"""
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE pct_chg > 9.5) AS up_limit,
               COUNT(*) FILTER (WHERE pct_chg < -9.5) AS down_limit,
               COUNT(*) FILTER (WHERE pct_chg < 0) AS down_count,
               COUNT(*) FILTER (WHERE pct_chg > 0) AS up_count,
               AVG(pct_chg) AS market_avg
        FROM daily_quotes WHERE trade_date=%s;
    """, (today,))
    r = cur.fetchone()
    if not r:
        return True, None
    up_limit = r['up_limit'] or 0
    down_limit = r['down_limit'] or 0
    avg = r['market_avg'] or 0
    
    if down_limit > up_limit * 3 and down_limit > 50:
        return False, f"市场极弱: 跌停 {down_limit} vs 涨停 {up_limit}, 暂不推荐"
    if avg < -3:
        return False, f"全市场平均跌 {avg:.2f}%, 暂不推荐"
    return True, None


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
    request_kwargs = {}
    if PROXY_URL:
        request_kwargs['proxy'] = PROXY_URL
    request = HTTPXRequest(**request_kwargs)
    
    bot = Bot(BOT_TOKEN, request=request)
    today = get_beijing_date()
    logger.info(f"=== 推送开始，today={today}, RUN_MODE={RUN_MODE} ===")
    
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    
    can_push, reason = check_market_state(cur, today)
    if not can_push:
        await bot.send_message(CHAT_ID, f"⚠️ <b>{today}</b>\n{reason}", parse_mode=ParseMode.HTML)
        cur.close(); conn.close()
        return
    
    cur.execute("""
        SELECT * FROM daily_candidates
        WHERE snapshot_date=%s AND selected=TRUE
        ORDER BY final_score DESC;
    """, (today,))
    cands = cur.fetchall()
    logger.info(f"查询 daily_candidates WHERE snapshot_date={today} AND selected=TRUE，找到 {len(cands)} 条")
    
    if not cands:
        cur.execute("""
            SELECT COUNT(*) as cnt FROM daily_candidates
            WHERE snapshot_date=%s;
        """, (today,))
        row = cur.fetchone()
        total = row['cnt'] if hasattr(row, 'keys') else row[0]
        msg = (f"📊 <b>{today}</b> {'盘前参考' if RUN_MODE == 'morning' else '盘后复盘'}\n\n"
               f"{'今日' if RUN_MODE == 'morning' else '明日'}无符合条件的推荐\n"
               f"（共分析 {total} 只候选股，均未达到阈值 {MIN_SELECT_SCORE} 分）\n\n"
               f"建议: 空仓观望 或 持有现有仓位")
        await bot.send_message(CHAT_ID, msg, parse_mode=ParseMode.HTML)
        cur.close(); conn.close()
        return
    
    mode_labels = {
        'morning': ('盘前参考', '今日'),
        'intraday': ('盘中速递', '当前'),
        'afternoon': ('盘后复盘', '明日'),
    }
    header, target = mode_labels.get(RUN_MODE, ('盘后复盘', '明日'))
    lines = [f" <b>{today}</b> {header}\n共 {len(cands)} 只\n"]
    
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
    
    cur.close(); conn.close()


if __name__ == '__main__':
    asyncio.run(push_daily_candidates())
