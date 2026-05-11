"""Telegram 推送 - 每日候选池 + 异动告警"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from psycopg2.extras import RealDictCursor
from telegram import Bot
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from loguru import logger

from core.db.connection import get_db
from core.utils.env import load_project_env
from core.utils.trading_calendar import is_trading_day as _calendar_is_trading_day

load_project_env()

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
PROXY_URL = os.getenv('TELEGRAM_PROXY')
RUN_MODE = os.getenv('RUN_MODE', 'morning')

# 北京时间时区
BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date():
    """获取北京时间日期（解决 GitHub Actions UTC 时区问题）"""
    return datetime.now(BEIJING_TZ).date()


def is_trading_day(d):
    """判断是否为交易日。委托给 core.utils.trading_calendar，含节假日识别。"""
    return _calendar_is_trading_day(d)

MIN_SELECT_SCORE = 45




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
    
    # 非交易日跳过推送
    if not is_trading_day(today):
        logger.warning(f"{today} 非交易日，跳过推送")
        return
    
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    
    can_push, reason = check_market_state(cur, today)
    if not can_push:
        await bot.send_message(CHAT_ID, f"⚠️ <b>{today}</b>\n{reason}", parse_mode=ParseMode.HTML)
        cur.close(); conn.close()
        return
    
    cur.execute("""
        SELECT * FROM daily_candidates
        WHERE snapshot_date=%s AND selected=TRUE AND source='llm_multisource' AND run_mode=%s
        ORDER BY final_score DESC;
    """, (today, RUN_MODE))
    llm_cands = cur.fetchall()
    logger.info(f"LLM候选: snapshot_date={today}, selected=TRUE, source='llm_multisource', run_mode={RUN_MODE} → {len(llm_cands)} 条")

    cur.execute("""
        SELECT * FROM daily_candidates
        WHERE snapshot_date=%s AND selected=TRUE AND source='overnight_8step'
        ORDER BY final_score DESC;
    """, (today,))
    step_cands = cur.fetchall()
    logger.info(f"八步法候选: snapshot_date={today}, selected=TRUE, source='overnight_8step' → {len(step_cands)} 条")

    if not llm_cands and not step_cands:
        cur.execute("""
            SELECT COUNT(*) as cnt FROM daily_candidates
            WHERE snapshot_date=%s;
        """, (today,))
        row = cur.fetchone()
        total = row['cnt'] if hasattr(row, 'keys') else row[0]
        msg = (f"📊 <b>{today}</b> {'盘前参考' if RUN_MODE == 'morning' else '盘后复盘'}\n\n"
               f"{'今日' if RUN_MODE == 'morning' else '明日'}无符合条件的推荐\n"
               f"（共分析 {total} 只候选股，均未达到阈值）\n\n"
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
    
    cur.execute("""
        SELECT source_name, COUNT(*) as cnt
        FROM raw_signals
        WHERE fetch_time >= %s
        GROUP BY source_name
        ORDER BY cnt DESC;
    """, (today,))
    source_stats = {row['source_name']: row['cnt'] for row in cur.fetchall()}
    
    all_sources = [
        'AKShare-龙虎榜',
        'AKShare-涨停板',
        'AKShare-个股研报',
        'AKShare-财经新闻',
        'AKShare-机构调研',
        'AKShare-热点概念',
    ]
    
    total_cands = len(llm_cands) + len(step_cands)
    stats_lines = []
    stats_lines.append(f" <b>{today}</b> {header}")
    stats_lines.append(f"共 {total_cands} 只\n")
    stats_lines.append("数据采集统计：")
    for src in all_sources:
        cnt = source_stats.get(src, 0)
        stats_lines.append(f"  {src}: {cnt} 条")
    stats_lines.append("")
    
    lines = stats_lines

    def _append_candidates(cands, emoji, label):
        if not cands:
            return
        lines.append(f"{emoji} <b>{label}</b> ({len(cands)} 只)")
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

    _append_candidates(llm_cands, "🤖", "LLM 多源策略")
    _append_candidates(step_cands, "🔮", "八步法")
    
    msg = '\n'.join(lines)
    
    try:
        await bot.send_message(CHAT_ID, msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"push failed: {e}")
    
    cur.close(); conn.close()


if __name__ == '__main__':
    asyncio.run(push_daily_candidates())
