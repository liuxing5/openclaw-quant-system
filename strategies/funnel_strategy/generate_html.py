"""
三策略对比 HTML 报告生成器
===========================
整合 七步漏斗 / LLM多源 / 八步法 到同一页面，支持暗/亮主题。
部署目标：https://liuxing5.github.io/openclaw-quant-system/funnel/funnel.html
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from dotenv import load_dotenv
for _env_path in [Path('.env'), Path('strategies/llm_multisource/.env')]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

from core.db.connection import get_db_fresh, get_db
from core.utils.env import load_project_env
from psycopg2.extras import RealDictCursor

load_project_env()

BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date():
    return datetime.now(BEIJING_TZ).date()


def query_dicts(sql, params=None):
    conn = get_db_fresh(use_dict_cursor=True)
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def query_one(sql, params=None):
    rows = query_dicts(sql, params)
    return rows[0] if rows else None


def clean_nan(obj):
    if isinstance(obj, dict):
        return {k: clean_nan(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_nan(item) for item in obj]
    elif isinstance(obj, float):
        if obj != obj:
            return None
        return obj
    return obj


# ---- 8步法步骤定义 ----
EIGHT_STEP_RULES = [
    ("step1", "涨幅筛选", "稳健3%-5%，高位6%-9.7%"),
    ("step2", "成交额", "稳健0.5-50亿，高位0.3-30亿"),
    ("step3", "换手率", "5%-10%"),
    ("step4", "市值过滤", "稳健50-500亿，高位30-200亿"),
    ("step5", "量比", "≥1"),
    ("step6", "均线+压力", "MA多头排列 + 无上方压力"),
    ("step7", "乖离率", "不超过5%"),
    ("step8", "综合评分", ">阈值 (85+)"),
]

# ---- LLM多源步骤定义 ----
LLM_STEPS = [
    ("step1", "多源信号采集", "6+ LLM源 → 提取推荐"),
    ("step2", "去重+标准化", "ts_code统一格式"),
    ("step3", "量化评分", "资金/形态/估值/财务/机构"),
    ("step4", "共识评分", "多源一致性 + 逻辑共振"),
    ("step5", "综合排序", "final_score ≥ 阈值"),
    ("step6", "精选标记", "Top N 标记 selected"),
]


def _get_latest_source_date(cur, source, lookback_days=7):
    ref_date = get_beijing_date()
    cur.execute("""
        SELECT MAX(snapshot_date) AS max_date
        FROM daily_candidates
        WHERE source = %s AND snapshot_date >= %s::date - %s;
    """, (source, ref_date, lookback_days))
    row = cur.fetchone()
    if row and row['max_date']:
        return row['max_date']
    return None


def load_funnel_data(trade_date=None):
    if trade_date:
        row = query_one("""
            SELECT * FROM funnel_results WHERE trade_date = %s ORDER BY trade_date DESC LIMIT 1;
        """, (trade_date,))
    else:
        row = query_one("""
            SELECT * FROM funnel_results ORDER BY trade_date DESC LIMIT 1;
        """)
    if not row:
        return None
    candidates = row.get('candidates')
    if isinstance(candidates, str):
        candidates = json.loads(candidates)
    row['candidates'] = clean_nan(candidates or [])
    return row


def load_candidates(source, trade_date=None, run_mode=None, retry_empty=False):
    conn = get_db_fresh(use_dict_cursor=True)
    cur = conn.cursor()
    
    if trade_date:
        latest = trade_date
    else:
        ref_date = get_beijing_date()
        cur.execute("""
            SELECT MAX(snapshot_date) AS max_date
            FROM daily_candidates
            WHERE source = %s AND snapshot_date >= %s::date - 7
              AND ts_code NOT LIKE '%%.AUDIT';
        """, (source, ref_date))
        row = cur.fetchone()
        if not row or not row['max_date']:
            cur.close(); conn.close()
            return [], None
        latest = row['max_date']
    
    if run_mode:
        cur.execute("""
            SELECT * FROM daily_candidates
            WHERE snapshot_date = %s AND source = %s AND run_mode = %s
            ORDER BY final_score DESC;
        """, (latest, source, run_mode))
    else:
        cur.execute("""
            SELECT * FROM daily_candidates
            WHERE snapshot_date = %s AND source = %s AND ts_code NOT LIKE '%%.AUDIT'
            ORDER BY final_score DESC;
        """, (latest, source))

    candidates = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    return candidates, latest


def load_scan_stats(strategy, snapshot_date=None):
    if snapshot_date:
        row = query_one("""
            SELECT * FROM strategy_scans
            WHERE strategy = %s AND snapshot_date = %s
            ORDER BY snapshot_date DESC LIMIT 1;
        """, (strategy, snapshot_date))
    else:
        row = query_one("""
            SELECT * FROM strategy_scans
            WHERE strategy = %s
            ORDER BY snapshot_date DESC LIMIT 1;
        """, (strategy,))
    if not row:
        return None
    stats = row.get('filter_stats')
    if isinstance(stats, str):
        stats = json.loads(stats)
    row['filter_stats'] = stats or {}
    return row


def generate_unified_html(output_dir=None, trade_date=None):
    if output_dir is None:
        output_dir = Path(__file__).parent / "docs"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 加载三策略数据 ──
    funnel = load_funnel_data(trade_date)
    funnel_date = str(funnel['trade_date']) if funnel else None

    llm_candidates, llm_date = load_candidates('llm_multisource', trade_date=trade_date, run_mode='morning')
    if not llm_candidates:
        llm_candidates, llm_date = load_candidates('llm_multisource', trade_date=trade_date)

    eight_candidates, eight_date = load_candidates('overnight_8step', trade_date=trade_date)
    # 并行 workflow 竞争：overnight_8step 和 funnel 同时 15:10 触发，
    # zuiyou1.py 可能还在写入，最多重试 5 次（每次 15s，共 75s）
    if not eight_candidates or not eight_date:
        import time as _time
        for _retry in range(5):
            _time.sleep(15)
            print(f"  ⏳ overnight_8step 数据为空，15s后重试 ({_retry+1}/5)...")
            eight_candidates, eight_date = load_candidates('overnight_8step', trade_date=trade_date)
            if eight_candidates:
                break
    eight_date_str = str(eight_date) if eight_date else None

    llm_scan = load_scan_stats('llm_multisource', llm_date)
    eight_scan = load_scan_stats('overnight_8step', eight_date)

    display_date = str(funnel_date or llm_date or eight_date or get_beijing_date())

    # ── 漏斗步骤 ──
    LAYER_RULES = {
        'L0': '上涨家数≥2500 且 全A指数>20EMA → 满仓；否则半仓或休战',
        'L1': '剔除ST/退市/次新股；流动比率>1.2；负债率<65%；营收同比>0%',
        'L2': '20日均成交额>1亿；流通市值>20亿；换手率3~15%',
        'L3': '周线CLOSE>20MA；EMA12>26>50多头排列；股价>EMA12',
        'L4': '量比1.5~3.0；乖离率<6%；需求吸收K线或强势接力形态',
        'L5': '综合评分≥80；涨幅3~5%；贴MA5；人气榜+LLM联动加分',
        'L6': '14:30后买入；止损=入场价-1ATR；目标=入场价+2ATR；盈亏比≥2:1',
    }
    SIGNAL_MAP = {
        'demand_absorption': '需求吸收',
        'strong_relay': '强势接力',
        'none': '无信号',
    }

    funnel_steps_html = ""
    if funnel:
        total = funnel['total_stocks']
        steps_data = [
            ("全市场", "📊", total, 0, "A股全市场日成交额>1亿", "#e3f2fd"),
            ("L0 大盘风控", "🌤️", total, 0,
             "✅满仓" if funnel['layer0_pass'] else "⚠️半仓", "#fff3e0"),
            ("L1 硬性防雷", "⚡", funnel['layer1_pass'], total - funnel['layer1_pass'],
             LAYER_RULES['L1'], "#fce4ec"),
            ("L2 流动性", "💧", funnel['layer2_pass'], funnel['layer1_pass'] - funnel['layer2_pass'],
             LAYER_RULES['L2'], "#f3e5f5"),
            ("L3 趋势结构", "📈", funnel['layer3_pass'], funnel['layer2_pass'] - funnel['layer3_pass'],
             LAYER_RULES['L3'], "#e8eaf6"),
            ("L4 动能信号", "🚀", funnel['layer4_pass'], funnel['layer3_pass'] - funnel['layer4_pass'],
             LAYER_RULES['L4'], "#e0f2f1"),
            ("L5 人气精选", "🔥", funnel['layer5_pass'], funnel['layer4_pass'] - funnel['layer5_pass'],
             LAYER_RULES['L5'], "#fff8e1"),
            ("L6 刚性风控", "🎯", funnel['layer6_pass'], funnel['layer5_pass'] - funnel['layer6_pass'],
             LAYER_RULES['L6'], "#e8f5e9"),
        ]
        for name, icon, cnt, elim, rule, color in steps_data:
            elim_html = f'<span class="elim">{elim}</span>' if elim else ''
            rule_html = f'<span class="rule-inline">{rule}</span>' if rule and len(rule) < 50 else ''
            funnel_steps_html += f"""
            <div class="step-row">
              <span class="step-icon">{icon}</span>
              <span class="step-name">{name}</span>
              <span class="step-pass">{cnt}</span>
              {elim_html}
              {rule_html}
            </div>"""

    # ── 八步法步骤 ──
    eight_steps_html = ""
    if eight_scan:
        stats = eight_scan.get('filter_stats', {})
        total_scanned = eight_scan.get('total_scanned', 0)
        total_passed = eight_scan.get('total_passed', 0)
        reject_map = {
            "涨幅不符": 0, "成交额": 0, "换手率": 0, "市值": 0,
            "量比": 0, "均线": 0, "压力": 0, "乖离严重": 0, "得分不足": 0,
        }
        for k, v in stats.items():
            reject_map[k] = reject_map.get(k, 0) + v

        sentiment = eight_scan.get('sentiment_score')
        mood = eight_scan.get('mood', '')
        mood_text = f"情绪{mood}({sentiment}分)" if sentiment else ""

        step_rules = {
            "涨幅筛选": "3%≤涨幅≤8%，剔除涨停/跌停",
            "成交额过滤": "成交额>2亿，保证流动性",
            "换手率过滤": "换手率3%~15%，活跃度适中",
            "市值过滤": "流通市值>30亿，剔除小盘股",
            "量比过滤": "量比>1.5，放量确认",
            "均线+压力": "5/10/20日均线多头，距压力位>3%",
            "乖离率过滤": "乖离率<8%，避免追高",
            "综合评分": "综合评分≥70，量化+情绪加权",
        }
        step_keys = [
            ("涨幅筛选", ["涨幅不符"]),
            ("成交额过滤", ["成交额"]),
            ("换手率过滤", ["换手率"]),
            ("市值过滤", ["市值"]),
            ("量比过滤", ["量比"]),
            ("均线+压力", ["均线", "压力"]),
            ("乖离率过滤", ["乖离严重"]),
            ("综合评分", ["得分不足"]),
        ]
        remaining = total_scanned
        for sname, keys in step_keys:
            elim = sum(reject_map.get(k, 0) for k in keys)
            remaining = max(0, remaining - elim)
            elim_html = f'<span class="elim">{elim}</span>' if elim else ''
            rule = step_rules.get(sname, '')
            rule_html = f'<span class="rule-inline">{rule}</span>' if rule else ''
            eight_steps_html += f"""
            <div class="step-row">
              <span class="step-name">{sname}</span>
              <span class="step-pass">{remaining}</span>
              {elim_html}
              {rule_html}
            </div>"""
        if mood_text:
            eight_steps_html += f'<div class="step-note"> {mood_text}</div>'

    # ── LLM多源步骤 ──
    llm_steps_html = ""
    if llm_scan:
        stats = llm_scan.get('filter_stats', {})
        llm_step_rules = {
            "多源信号采集": "龙虎榜/涨停/研报/公告/调研多源聚合",
            "综合评分≥阈值": "量化评分+LLM评分加权≥60",
            "精选标记": "人工精选+LLM二次确认",
        }
        llm_steps_html += f"""
        <div class="step-row">
          <span class="step-name">多源信号采集</span>
          <span class="step-pass">{stats.get('多源聚合后', '—')}</span>
          <span class="rule-inline">{llm_step_rules['多源信号采集']}</span>
        </div>
        <div class="step-row">
          <span class="step-name">综合评分≥阈值</span>
          <span class="step-pass">{stats.get('综合评分≥阈值', '—')}</span>
          <span class="rule-inline">{llm_step_rules['综合评分≥阈值']}</span>
        </div>
        <div class="step-row">
          <span class="step-name">精选标记</span>
          <span class="step-pass">{stats.get('精选标记', '—')}</span>
          <span class="rule-inline">{llm_step_rules['精选标记']}</span>
        </div>"""

    # ── 候选卡片渲染 ──
    def render_candidate_card(c, badge_class='', badge_text=''):
        code = c.get('ts_code', '')
        name = c.get('stock_name', '')
        score = c.get('final_score', c.get('score', 0))
        quant = c.get('quant_score', 0)
        llm_score = c.get('llm_score', 0)
        entry_l = c.get('entry_low') or c.get('entry_price')
        stop_l = c.get('stop_loss')
        target = c.get('target_1') or c.get('target_price')
        tags = c.get('logic_tags', [])
        if isinstance(tags, str):
            tags = tags.replace('/', '|').split('|')
        tags = [t.strip() for t in tags if t.strip()][:3]
        selected = c.get('selected', False)
        sel_cls = 'selected' if selected else ''

        src_badge = ''
        source_list = c.get('sources')
        if source_list and isinstance(source_list, list) and len(source_list) > 0:
            src_badge = f'<span class="src-badge">{len(source_list)}源</span>'

        entry_fmt = f'{entry_l:.2f}' if entry_l else '—'
        stop_fmt = f'{stop_l:.2f}' if stop_l else '—'
        target_fmt = f'{target:.2f}' if target else '—'

        return f"""
        <div class="candidate-card {sel_cls}">
          <div class="cand-header">
            <div>
              <span class="cand-code">{code}</span>
              <span class="cand-name">{name or ''}</span>
              {badge_text}
              {src_badge}
            </div>
            <span class="cand-score">{score:.0f}</span>
          </div>
          <div class="cand-metrics">
            <div class="metric"><span class="mv">{entry_fmt}</span><span class="ml">入场</span></div>
            <div class="metric stop"><span class="mv">{stop_fmt}</span><span class="ml">止损</span></div>
            <div class="metric target"><span class="mv">{target_fmt}</span><span class="ml">目标</span></div>
            <div class="metric"><span class="mv">{quant:.0f}</span><span class="ml">量化分</span></div>
          </div>
          {f'<div class="cand-tags">{" · ".join(tags)}</div>' if tags else ''}
        </div>"""

    funnel_cards = ""
    if funnel and funnel.get('candidates'):
        for c in funnel['candidates']:
            signal = c.get('signal_type', '')
            signal_text = SIGNAL_MAP.get(signal, signal)
            llm_bonus = c.get('llm_bonus', 0)
            bonus_text = f' <span class="llm-badge">LLM+{llm_bonus}</span>' if llm_bonus > 0 else ''
            card = render_candidate_card(c, badge_text=f'<span class="signal-badge">{signal_text}</span>{bonus_text}')
            funnel_cards += card
    if not funnel_cards:
        funnel_cards = '<div class="no-data">今日无推荐</div>'

    llm_cards = ""
    if llm_candidates:
        for c in llm_candidates:
            card = render_candidate_card(c)
            llm_cards += card
    if not llm_cards:
        llm_cards = '<div class="no-data">今日无推荐</div>'

    eight_cards = ""
    if eight_candidates:
        for c in eight_candidates:
            card = render_candidate_card(c, badge_class='badge-8step', badge_text='<span class="badge-8step">八步法</span>')
            eight_cards += card
    if not eight_cards:
        eight_cards = '<div class="no-data">今日无推荐</div>'

    # ── 历史日期 ──
    history_dates_raw = query_dicts("""
        SELECT DISTINCT trade_date FROM funnel_results ORDER BY trade_date DESC LIMIT 10;
    """)
    hd_list = [str(r['trade_date']) for r in history_dates_raw]
    if funnel_date and funnel_date not in hd_list:
        hd_list.insert(0, funnel_date)
    if eight_date_str and eight_date_str not in hd_list:
        hd_list.insert(0, eight_date_str)
    history_opts = ''.join(f'<option value="{d}" {("selected" if d == display_date else "")}>{d}</option>' for d in hd_list[:10])

    # ── 汇总统计 ──
    funnel_count = len(funnel['candidates']) if funnel and funnel.get('candidates') else 0
    funnel_elapsed = funnel.get('elapsed_seconds', 0) if funnel else 0
    llm_count = len(llm_candidates)
    eight_count = len(eight_candidates)

    gen_time = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')

    # ════════════════════════════════════════════════════════════
    # HTML 模板
    # ════════════════════════════════════════════════════════════
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>三策略对比 - {display_date}</title>
<style>
:root {{
  --bg: #f0f2f5; --card: #fff; --text: #1a1a1a; --text2: #666; --border: #e0e0e0;
  --metric-bg: #f5f5f5; --header-bg: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  --header-text: #fff; --score-color: #4caf50; --stop-color: #f44336;
  --target-color: #4caf50; --badge-bg: #e3f2fd; --badge-text: #1976d2;
  --sel-border: #4caf50; --elim-color: #f44336; --step-row-hover: #f5f5f5;
  --tab-active: #667eea; --tab-active-text: #fff; --tab-inactive: #e0e0e0;
  --section-bg: #fff; --shadow: 0 2px 8px rgba(0,0,0,0.08);
  --llm-badge-bg: #f3e5f5; --llm-badge-text: #9c27b0;
  --8step-badge-bg: #ede7f6; --8step-badge-text: #512da8;
  --funnel-badge-bg: #e8f5e9; --funnel-badge-text: #2e7d32;
}}
[data-theme="dark"] {{
  --bg: #0d1117; --card: #161b22; --text: #c9d1d9; --text2: #8b949e;
  --border: #30363d; --metric-bg: #21262d; --header-bg: linear-gradient(135deg, #1f2937 0%, #111827 100%);
  --header-text: #e5e7eb; --score-color: #3fb950; --stop-color: #f85149;
  --target-color: #3fb950; --badge-bg: #1f6feb22; --badge-text: #58a6ff;
  --sel-border: #3fb950; --elim-color: #f85149; --step-row-hover: #1c2128;
  --tab-active: #58a6ff; --tab-active-text: #fff; --tab-inactive: #21262d;
  --section-bg: #161b22; --shadow: 0 2px 8px rgba(0,0,0,0.4);
  --llm-badge-bg: #d2992233; --llm-badge-text: #e3b341;
  --8step-badge-bg: #a371f733; --8step-badge-text: #a371f7;
  --funnel-badge-bg: #3fb95033; --funnel-badge-text: #3fb950;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); min-height:100vh; transition:background .3s,color .3s; }}
.container {{ max-width:1400px; margin:0 auto; padding:16px; }}

/* Header */
.header {{ background:var(--header-bg); color:var(--header-text); padding:28px 24px; border-radius:12px; margin-bottom:16px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; }}
.header h1 {{ font-size:1.4rem; margin-bottom:6px; }}
.header .sub {{ font-size:.82rem; opacity:.85; }}
.header .date-select {{ padding:7px 14px; border-radius:6px; border:1px solid rgba(255,255,255,.3); background:rgba(255,255,255,.15); color:var(--header-text); font-size:13px; cursor:pointer; }}
.theme-btn {{ background:rgba(255,255,255,.2); color:var(--header-text); border:none; padding:8px 16px; border-radius:20px; cursor:pointer; font-size:14px; transition:background .2s; }}
.theme-btn:hover {{ background:rgba(255,255,255,.3); }}

/* Section */
.section {{ background:var(--section-bg); border-radius:10px; padding:20px; margin-bottom:16px; box-shadow:var(--shadow); border:1px solid var(--border); }}
.section h2 {{ font-size:1.05rem; margin-bottom:14px; padding-bottom:8px; border-bottom:2px solid var(--tab-active); }}
.section-subtitle {{ font-size:.78rem; color:var(--text2); margin-left:8px; font-weight:normal; }}

/* Three column layout */
.three-col {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; align-items:stretch; }}
.three-col .section {{ margin-bottom:0; height:100%; display:flex; flex-direction:column; }}
.three-col .cards-grid {{ flex:1; }}
@media (max-width:1024px) {{ .three-col {{ grid-template-columns:1fr; }} }}

/* Funnel steps */
.step-row {{ display:flex; align-items:center; gap:8px; padding:7px 10px; border-radius:6px; font-size:.82rem; transition:background .15s; }}
.step-row:hover {{ background:var(--step-row-hover); }}
.step-icon {{ width:22px; text-align:center; }}
.step-name {{ font-weight:500; min-width:80px; }}
.step-pass {{ font-weight:bold; color:var(--score-color); min-width:36px; text-align:right; }}
.step-row .elim {{ color:var(--elim-color); font-size:.72rem; margin-left:4px; }}
.step-row .rule-inline {{ color:var(--text2); font-size:.7rem; margin-left:auto; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:220px; }}
.step-note {{ font-size:.75rem; color:var(--text2); padding:6px 10px; }}

/* Candidate cards */
.cards-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:10px; }}
.candidate-card {{ background:var(--metric-bg); border-radius:8px; padding:12px; border:1px solid var(--border); transition:transform .15s,box-shadow .15s; }}
.candidate-card:hover {{ transform:translateY(-2px); box-shadow:var(--shadow); }}
.candidate-card.selected {{ border-color:var(--sel-border); }}
.cand-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
.cand-code {{ font-weight:bold; font-size:.92rem; }}
.cand-name {{ font-size:.78rem; color:var(--text2); margin-left:4px; }}
.cand-score {{ font-size:1.15rem; font-weight:bold; color:var(--score-color); }}
.cand-metrics {{ display:grid; grid-template-columns:1fr 1fr; gap:4px; }}
.metric {{ text-align:center; padding:5px 4px; background:var(--card); border-radius:4px; }}
.metric .mv {{ font-size:.85rem; font-weight:bold; }}
.metric .ml {{ font-size:.65rem; color:var(--text2); display:block; }}
.metric.stop .mv {{ color:var(--stop-color); }}
.metric.target .mv {{ color:var(--target-color); }}
.cand-tags {{ font-size:.7rem; color:var(--text2); margin-top:8px; padding:4px 8px; background:var(--card); border-radius:4px; }}

/* Badges */
.badge-8step {{ display:inline-block; padding:2px 7px; border-radius:10px; font-size:.68rem; font-weight:bold; background:var(--8step-badge-bg); color:var(--8step-badge-text); margin-left:4px; }}
.signal-badge {{ display:inline-block; padding:2px 7px; border-radius:10px; font-size:.68rem; background:var(--badge-bg); color:var(--badge-text); margin-left:4px; }}
.llm-badge {{ display:inline-block; padding:2px 7px; border-radius:10px; font-size:.68rem; background:var(--llm-badge-bg); color:var(--llm-badge-text); margin-left:4px; }}
.src-badge {{ display:inline-block; padding:2px 6px; border-radius:10px; font-size:.65rem; background:var(--badge-bg); color:var(--badge-text); margin-left:4px; }}

/* Tabs */
.tabs {{ display:flex; gap:4px; margin-bottom:14px; flex-wrap:wrap; }}
.tab-btn {{ padding:6px 16px; border-radius:16px; border:none; cursor:pointer; font-size:.8rem; background:var(--tab-inactive); color:var(--text); transition:all .2s; }}
.tab-btn.active {{ background:var(--tab-active); color:var(--tab-active-text); }}

/* No data */
.no-data {{ text-align:center; padding:30px; color:var(--text2); font-size:.88rem; }}

/* Footer */
.footer {{ text-align:center; padding:20px; color:var(--text2); font-size:.75rem; }}

/* Legend */
.legend {{ display:flex; gap:14px; flex-wrap:wrap; font-size:.75rem; color:var(--text2); margin-bottom:12px; }}
.legend-item {{ display:flex; align-items:center; gap:4px; }}
.legend-dot {{ width:10px; height:10px; border-radius:50%; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1>📊 三策略对比选股系统</h1>
      <div class="sub">
        报告日期: {display_date} | 生成: {gen_time}
        <select class="date-select" onchange="switchDate(this.value)">
          <option value="">历史日期</option>
          {history_opts}
        </select>
      </div>
    </div>
    <button class="theme-btn" onclick="toggleTheme()" id="themeBtn">
      <span id="themeIcon">🌙</span> 深色模式
    </button>
  </div>

  <div class="legend">
    <div class="legend-item"><div class="legend-dot" style="background:var(--score-color)"></div> 通过数</div>
    <div class="legend-item"><div class="legend-dot" style="background:var(--elim-color)"></div> 淘汰数</div>
    <div class="legend-item"><span class="badge-8step">八步法</span> 八步法标记</div>
    <div class="legend-item"><span class="llm-badge">LLM+</span> LLM联动加分</div>
    <div class="legend-item"><span class="signal-badge">信号</span> 买入信号</div>
  </div>

  <div class="three-col">
    <!-- ========== 七步漏斗 ========== -->
    <div class="section">
      <h2>🎯 七步漏斗选股<span class="section-subtitle">大盘环境→防雷→流动性→趋势→动能→人气→风控</span></h2>
      {funnel_steps_html}
      <h3 style="font-size:.9rem;margin:14px 0 8px;color:var(--text);">最终推荐 ({funnel_count}只)</h3>
      <div class="cards-grid">{funnel_cards}</div>
    </div>

    <!-- ========== LLM多源 ========== -->
    <div class="section">
      <h2>🤖 LLM多源策略<span class="section-subtitle">{llm_date or '—'}</span></h2>
      {llm_steps_html}
      <h3 style="font-size:.9rem;margin:14px 0 8px;color:var(--text);">候选标的 ({llm_count}只)</h3>
      <div class="cards-grid">{llm_cards}</div>
    </div>

    <!-- ========== 八步法 ========== -->
    <div class="section">
      <h2>🔮 八步隔夜法<span class="section-subtitle">{eight_date_str or '—'}</span></h2>
      {eight_steps_html}
      <h3 style="font-size:.9rem;margin:14px 0 8px;color:var(--text);">候选标的 ({eight_count}只)</h3>
      <div class="cards-grid">{eight_cards}</div>
    </div>
  </div>

  <div class="footer">
    AI Stock Recommendation System | 三策略对比 · 每日15:10自动更新
  </div>
</div>

<script>
function toggleTheme() {{
  const html = document.documentElement;
  const isDark = html.getAttribute('data-theme') === 'dark';
  if (isDark) {{
    html.removeAttribute('data-theme');
    localStorage.setItem('theme', 'light');
    document.getElementById('themeIcon').textContent = '🌙';
    document.getElementById('themeBtn').innerHTML = '<span id="themeIcon">🌙</span> 深色模式';
  }} else {{
    html.setAttribute('data-theme', 'dark');
    localStorage.setItem('theme', 'dark');
    document.getElementById('themeIcon').textContent = '☀️';
    document.getElementById('themeBtn').innerHTML = '<span id="themeIcon">☀️</span> 浅色模式';
  }}
}}
function switchDate(dateVal) {{
  if (!dateVal) return;
  window.location.href = 'funnel-' + dateVal + '.html';
}}
(function() {{
  const saved = localStorage.getItem('theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme:dark)').matches;
  if (saved === 'dark' || (!saved && prefersDark)) {{
    document.documentElement.setAttribute('data-theme', 'dark');
    document.getElementById('themeIcon').textContent = '☀️';
    document.getElementById('themeBtn').innerHTML = '<span id="themeIcon">☀️</span> 浅色模式';
  }}
}})();
</script>
</body>
</html>'''

    # 写入文件
    if trade_date:
        output_file = output_dir / f"funnel-{trade_date}.html"
    else:
        output_file = output_dir / "funnel.html"
    output_file.write_text(html, encoding='utf-8')
    print(f"  ✓ 三策略对比 HTML: {output_file}")

    if trade_date:
        index_file = output_dir / "index.html"
        index_html = f'''<!DOCTYPE html>
<html><head>
<meta http-equiv="refresh" content="0; url=funnel.html">
<title>三策略对比</title>
</head><body>
<p>跳转到 <a href="funnel.html">三策略对比页面</a></p>
</body></html>'''
        index_file.write_text(index_html, encoding='utf-8')

    return output_file


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="生成三策略对比 HTML 报告")
    parser.add_argument("--date", "-d", type=str, default=None, help="日期 (YYYY-MM-DD)")
    parser.add_argument("--output", "-o", type=str, default=None, help="输出目录")
    args = parser.parse_args()

    output_dir = args.output
    generate_unified_html(output_dir=output_dir, trade_date=args.date)
