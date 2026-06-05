"""
统一每日策略推送模块
========================================
从数据库读取4策略当日候选，推送汇总消息到 Telegram。

4个策略：
  - 漏斗 (funnel_strategy)
  - LLM多源 (llm_multisource)
  - 八步隔夜法 (overnight_8step)
  - 主升浪 (main_uptrend)

用法：
  from strategies.shared.daily_notify import push_daily_summary
  push_daily_summary(trade_date="2026-05-30")
"""
from __future__ import annotations

import json
import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from dotenv import load_dotenv
from pathlib import Path

for _env_path in [Path('.env'), Path('strategies/llm_multisource/.env')]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MAX_MSG_LENGTH = 4000


def _send_telegram(text: str, parse_mode: Optional[str] = None) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram 未配置 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，跳过推送")
        return False
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code == 200:
            return True
        logger.warning(f"Telegram 推送失败: {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Telegram 推送异常: {e}")
        return False


def _send_long_telegram(text: str, parse_mode: Optional[str] = None) -> bool:
    if len(text) <= MAX_MSG_LENGTH:
        return _send_telegram(text, parse_mode)
    import time
    lines = text.split("\n")
    chunks = []
    current = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > MAX_MSG_LENGTH:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    all_ok = True
    for i, chunk in enumerate(chunks, 1):
        prefix = f"[{i}/{len(chunks)}]\n" if len(chunks) > 1 else ""
        ok = _send_telegram(prefix + chunk, parse_mode)
        if not ok:
            all_ok = False
        time.sleep(0.5)
    return all_ok


def _get_conn():
    from core.db.connection import get_db_fresh
    return get_db_fresh(use_dict_cursor=True)


def _release_conn(conn):
    try:
        conn.close()
    except Exception:
        pass


def _load_candidates_from_db(conn, source: str, trade_date: str) -> list:
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT ts_code, stock_name, final_score, logic_tags, selected,
                   position_pct, stop_loss, target_1, sources, run_mode
            FROM daily_candidates
            WHERE snapshot_date = %s AND source = %s AND ts_code NOT LIKE '%%.AUDIT'
            ORDER BY final_score DESC;
        """, (trade_date, source))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows
    except Exception as e:
        logger.warning(f"加载 {source} 候选失败: {e}")
        return []


def _load_funnel_summary(conn, trade_date: str) -> Optional[dict]:
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT trade_date, total_stocks, layer0_pass, layer1_pass,
                   layer2_pass, layer3_pass, layer4_pass, layer5_pass, layer6_pass
            FROM funnel_results WHERE trade_date = %s;
        """, (trade_date,))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"加载漏斗汇总失败: {e}")
        return None


def _load_uptrend_stats(conn, trade_date: str) -> Optional[dict]:
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT run_date, a_pool_size, b_signals, c_signals, d_passed, candidates
            FROM main_uptrend_runs WHERE run_date = %s;
        """, (trade_date,))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"加载主升浪统计失败: {e}")
        return None


def _load_scan_stats(conn, strategy: str, trade_date: str) -> Optional[dict]:
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT total_scanned, total_passed, filter_stats
            FROM strategy_scans
            WHERE strategy = %s AND snapshot_date = %s;
        """, (strategy, trade_date))
        row = cur.fetchone()
        cur.close()
        if row:
            result = dict(row)
            if isinstance(result.get('filter_stats'), str):
                result['filter_stats'] = json.loads(result['filter_stats'])
            return result
        return None
    except Exception as e:
        logger.warning(f"加载 {strategy} 扫描统计失败: {e}")
        return None


def _format_stock_line(c: dict, source_label: str) -> str:
    code = c.get('ts_code', '')
    name = c.get('stock_name', '')
    score = c.get('final_score', 0)
    score_str = f"{score:.1f}" if isinstance(score, (int, float)) else str(score)
    pos = c.get('position_pct', 0)
    pos_str = f" 仓位{pos:.0f}%" if pos else ""
    stop = c.get('stop_loss')
    stop_str = f" 止损{stop:.2f}" if stop else ""
    tags = c.get('logic_tags', [])
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = [tags]
    tag_str = ' '.join(str(t) for t in tags[:3]) if tags else ''
    short_code = code.replace('.SZ', '').replace('.SH', '')
    return f"  • {short_code} {name} 得分{score_str}{pos_str}{stop_str} {tag_str}"


def push_daily_summary(trade_date: Optional[str] = None) -> bool:
    now_beijing = datetime.now(BEIJING_TZ)
    if not trade_date:
        trade_date = now_beijing.strftime("%Y-%m-%d")

    # 每日汇总推送只在下午15:00后执行（防止早上 push/workflow_dispatch 触发误发）
    if now_beijing.hour < 15:
        logger.warning(f"北京时间 {now_beijing.strftime('%H:%M')} < 15:00，跳过每日汇总推送")
        return False

    try:
        conn = _get_conn()
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        return False

    try:
        lines = []
        lines.append(f"📊 OpenClaw 每日策略汇总")
        lines.append(f"📅 {trade_date}")
        lines.append("")

        # ── 漏斗策略 ──
        funnel_summary = _load_funnel_summary(conn, trade_date)
        funnel_candidates = _load_candidates_from_db(conn, 'funnel_strategy', trade_date)
        lines.append("━━ 漏斗策略 ━━")
        if funnel_summary:
            l6 = funnel_summary.get('layer6_pass', 0)
            l0 = funnel_summary.get('layer0_pass', 0)
            total = funnel_summary.get('total_stocks', 0)
            if l0 == 0:
                lines.append("  ⚠️ L0大盘风控未通过，不荐股")
            elif l6 > 0:
                lines.append(f"  全市场{total} → L6通过{l6}只")
                for c in funnel_candidates[:5]:
                    lines.append(_format_stock_line(c, '漏斗'))
                if len(funnel_candidates) > 5:
                    lines.append(f"  ... 等{len(funnel_candidates)}只")
            else:
                lines.append(f"  全市场{total} → 无最终候选")
        else:
            lines.append("  暂无数据")

        # ── LLM多源 ──
        llm_candidates = _load_candidates_from_db(conn, 'llm_multisource', trade_date)
        lines.append("")
        lines.append("━━ LLM多源 ━━")
        if llm_candidates:
            selected = [c for c in llm_candidates if c.get('selected')]
            if selected:
                lines.append(f"  精选{len(selected)}只 (共{len(llm_candidates)}条)")
                for c in selected[:5]:
                    lines.append(_format_stock_line(c, 'LLM'))
                if len(selected) > 5:
                    lines.append(f"  ... 等{len(selected)}只")
            else:
                lines.append(f"  共{len(llm_candidates)}条观察，无精选")
        else:
            lines.append("  暂无数据")

        # ── 八步隔夜法 ──
        eight_candidates = _load_candidates_from_db(conn, 'overnight_8step', trade_date)
        lines.append("")
        lines.append("━━ 八步隔夜法 ━━")
        if eight_candidates:
            # 按 ts_code 去重，保留得分最高的记录
            best_by_code = {}
            for c in eight_candidates:
                code = c.get('ts_code', '')
                score = c.get('final_score', 0) or 0
                if code not in best_by_code or score > (best_by_code[code].get('final_score', 0) or 0):
                    best_by_code[code] = c
            eight_candidates = list(best_by_code.values())

            stable = [c for c in eight_candidates if '稳健' in str(c.get('logic_tags', []))]
            upper = [c for c in eight_candidates if '高位' in str(c.get('logic_tags', []))]
            others = [c for c in eight_candidates if c not in stable and c not in upper]
            if stable:
                lines.append(f"  稳健路径({len(stable)}只):")
                for c in stable[:3]:
                    lines.append(_format_stock_line(c, '八步'))
            if upper:
                lines.append(f"  高位路径({len(upper)}只):")
                for c in upper[:3]:
                    lines.append(_format_stock_line(c, '八步'))
            if others and not stable and not upper:
                lines.append(f"  候选{len(others)}只:")
                for c in others[:5]:
                    lines.append(_format_stock_line(c, '八步'))
        else:
            lines.append("  暂无数据")

        # ── 主升浪 ──
        uptrend_candidates = _load_candidates_from_db(conn, 'main_uptrend', trade_date)
        uptrend_stats = _load_uptrend_stats(conn, trade_date)
        lines.append("")
        lines.append("━━ 主升浪 ━━")
        if uptrend_stats:
            a = uptrend_stats.get('a_pool_size', 0)
            b = uptrend_stats.get('b_signals', 0)
            c_cnt = uptrend_stats.get('c_signals', 0)
            d = uptrend_stats.get('d_passed', 0)
            n = uptrend_stats.get('candidates', 0)
            lines.append(f"  A:{a} B:{b} C:{c_cnt} D:{d} → 候选{n}只")
        if uptrend_candidates:
            for c in uptrend_candidates[:5]:
                lines.append(_format_stock_line(c, '主升浪'))
            if len(uptrend_candidates) > 5:
                lines.append(f"  ... 等{len(uptrend_candidates)}只")
        elif not uptrend_stats:
            lines.append("  暂无数据")

        lines.append("")
        lines.append("🔗 详细报告: https://liuxing5.github.io/openclaw-quant-system/funnel/funnel.html")

        msg = "\n".join(lines)
        return _send_long_telegram(msg)
    finally:
        _release_conn(conn)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="每日策略汇总推送")
    parser.add_argument("--date", type=str, default=None, help="交易日期 YYYY-MM-DD")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    ok = push_daily_summary(args.date)
    print(f"推送{'成功' if ok else '失败'}")
