"""
五策略对比 HTML 报告生成器
===========================
整合 七步漏斗 / LLM多源 / 八步法 到同一页面，支持暗/亮主题。
部署目标：https://liuxing5.github.io/openclaw-quant-system/funnel/funnel.html
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from html import escape as html_escape
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from dotenv import load_dotenv
for _env_path in [Path('.env'), Path('strategies/llm_multisource/.env')]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

import psycopg2  # load_candidates 里显式引用 psycopg2.OperationalError，必须导入
from core.db.connection import get_db_fresh
from core.utils.env import load_project_env
from psycopg2.extras import RealDictCursor

from strategies.funnel_strategy.funnel_config import DEFAULT_FUNNEL_CONFIG
from strategies.shared.backtest_stats import COST_NOTE, SIGNIFICANCE_NOTE

load_project_env()

BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date():
    return datetime.now(BEIJING_TZ).date()


def _close_conn(conn):
    """把连接归还连接池（get_db_fresh 拿到的连接必须显式 close 才会归还）。

    忘记归还会导致 "connection pool exhausted"，进程在第 6 次查询时直接崩溃。
    """
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


def query_dicts(sql, params=None):
    """Execute query and return rows.

    Connection pool exhaustion retry is handled at _connect() level in core/db/connection.py.
    """
    conn = get_db_fresh(use_dict_cursor=True)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        _close_conn(conn)


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


def _fnum(v, default=0.0):
    """数值兜底：None / NaN / 非数值 → default（final_score 等列可为 NULL）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if f != f else f


def build_layer_rules(cfg=None):
    """L0~L6 漏斗文案 —— 全部由 FunnelConfig 动态生成，阈值改了文案跟着改。

    2026-10-14 审查：此前 L0/L1/L2/L4/L5 文案与 DEFAULT_FUNNEL_CONFIG 不符
    （上涨占比写 35% 实际 30%、营收同比写 >0% 实际 -10%、流通市值写 20亿 实际 50亿、
    综合评分写 ≥80 实际 70、涨幅写 3~5% 实际 2~6%），L4 还把两条剔除条件写成了
    通过条件。现统一读配置，方向性条件按"通过 vs 剔除"如实描述。
    """
    cfg = cfg or DEFAULT_FUNNEL_CONFIG

    def _yi(v):
        """元 → 亿（去掉多余小数）。"""
        return f"{v / 1e8:g}"

    def _yi_cap(v):
        """元 → 万亿/亿 自适应。"""
        return f"{v / 1e8:g}亿"

    return {
        'L0': (f"上涨占比≥{cfg.layer0_min_breadth_ratio:.0%} 且 广度>"
               f"{cfg.layer0_index_ema_period}EMA → 满仓；否则限仓≤"
               f"{cfg.layer0_partial_cap:.0%}（绝对家数阈值已退出判定，2026-10-13审查）"),
        'L1': (f"剔除ST/退市/次新股(上市<{cfg.layer1_exclude_new_ipo_days}天)；"
               f"流动比率>{cfg.layer1_min_current_ratio}；负债率<{cfg.layer1_max_debt_ratio:g}%；"
               f"营收同比≥{cfg.layer1_min_revenue_yoy:g}%（连续负增≤"
               f"{cfg.layer1_max_consecutive_rev_decline}次）"),
        'L2': (f"20日均成交额>{_yi(cfg.layer2_min_avg_amount_20d)}亿；"
               f"流通市值{_yi_cap(cfg.layer2_min_circulating_mcap)}~"
               f"{_yi_cap(cfg.layer2_max_circulating_mcap)}；"
               f"换手率{cfg.layer2_turn_rate_min:g}~{cfg.layer2_turn_rate_max:g}%"
               f"（⚠绝对阈值待分位数化）"),
        'L3': (f"周线CLOSE>{cfg.layer3_weekly_ma_period}MA；"
               f"EMA{cfg.layer3_ema_fast}>{cfg.layer3_ema_mid}>{cfg.layer3_ema_slow}多头排列；"
               f"股价>EMA{cfg.layer3_ema_fast}（剔除：RSI>{cfg.layer3_max_rsi:g} 超买）"),
        'L4': (f"通过条件：量比{cfg.layer4_volume_ratio_min:g}~{cfg.layer4_volume_ratio_max:g} + "
               f"买入信号（回踩反弹/需求吸收/强势接力，三者皆无时以 trend_continuation "
               f"EMA{cfg.layer3_ema_fast}>{cfg.layer3_ema_mid} 多头+阳线+量比达标兜底）；"
               f"剔除条件：RSI>{cfg.layer3_max_rsi:g}（L3层）、"
               f"近{cfg.layer4_recent_gain_days}日涨幅>{cfg.layer4_max_recent_gain_pct:g}%、"
               f"天量上轨（乖离率/EMA12距离两项冗余条件已停用）"),
        'L5': (f"综合评分≥{cfg.layer5_min_composite_score}；"
               f"涨幅{cfg.layer5_pct_range_low:g}~{cfg.layer5_pct_range_high:g}%；贴MA5；"
               f"人气榜≤{cfg.layer5_popularity_rank_threshold} + LLM联动加分"),
        'L6': (f"{cfg.layer6_entry_after_time}后买入；止损=入场价-1ATR（上限"
               f"{cfg.layer6_max_stop_loss_pct:g}%）；目标=入场价+{cfg.layer6_target_atr_mult:g}ATR；"
               f"盈亏比≥{cfg.layer6_min_profit_loss_ratio:g}:1"),
    }


def stale_data_note(data_date, label="候选数据"):
    """候选数据日期滞后提示（load_candidates 只回看 7 天，可能拿到非当日数据）。

    2026-10-14 审查新增：卡片区域显示数据日期 + 滞后天数，>3 天标红。
    """
    if not data_date:
        return ""
    try:
        d = data_date if isinstance(data_date, date) else date.fromisoformat(str(data_date)[:10])
    except (TypeError, ValueError):
        return ""
    lag = (get_beijing_date() - d).days
    if lag <= 0:
        return ""
    color = "#f85149" if lag > 3 else "#f0883e"
    return (f'<div style="font-size:.66rem;color:{color};margin:4px 0 6px;">'
            f'⚠ {label}日期 <strong>{d}</strong> · 滞后 <strong>{lag}</strong> 天'
            f'（非当日数据，回看窗口 7 天）</div>')


def fwd_generated_note(fwd_json):
    """前向收益 JSON 的生成时间提示：>14 天变色告警。"""
    if not fwd_json:
        return ""
    gen = str(fwd_json.get("generated_at") or "")[:10]
    data_as_of = str(fwd_json.get("data_as_of") or "")[:10]
    if not gen:
        return ""
    try:
        lag = (get_beijing_date() - date.fromisoformat(gen)).days
    except ValueError:
        lag = None
    parts = [f"生成于 {gen}（北京时间）"]
    if data_as_of:
        parts.append(f"数据截止 {data_as_of}")
    if lag is not None and lag > 14:
        parts.append(f"⚠ 已 {lag} 天未更新")
        color = "#f85149"
    elif lag is not None and lag > 0:
        parts.append(f"{lag} 天前")
        color = "#f0883e"
    else:
        color = "var(--text-secondary)"
    return (f'<div style="font-size:.64rem;color:{color};margin-top:2px;">'
            f'{" · ".join(parts)}</div>')


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
    # 确保 trade_date 是 date 对象，psycopg2 才能正确匹配 DATE 字段
    if trade_date and isinstance(trade_date, str):
        try:
            trade_date = date.fromisoformat(trade_date)
        except ValueError:
            pass
    
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
    candidates = clean_nan(candidates or [])

    # 补充 stock_name：漏斗流程中 stock_name 可能在后续层丢失
    codes_missing_name = [c.get('ts_code', '') for c in candidates if not c.get('stock_name')]
    if codes_missing_name:
        try:
            conn = get_db_fresh(use_dict_cursor=True)
            try:
                cur = conn.cursor()
                cur.execute("SELECT ts_code, stock_name FROM stock_basic_info WHERE ts_code = ANY(%s);", (codes_missing_name,))
                name_map = {r['ts_code']: r['stock_name'] for r in cur.fetchall()}
                cur.close()
            finally:
                _close_conn(conn)
            for c in candidates:
                if not c.get('stock_name'):
                    c['stock_name'] = name_map.get(c.get('ts_code', ''), '')
        except Exception:
            pass

    row['candidates'] = candidates
    return row


def load_candidates(source, trade_date=None, run_mode=None, retry_empty=False, max_retries=5):
    """Load candidates with SSL retry logic."""
    import time as _t
    # 确保 trade_date 是 date 对象，psycopg2 才能正确匹配 DATE 字段
    if trade_date and isinstance(trade_date, str):
        try:
            trade_date = date.fromisoformat(trade_date)
        except ValueError:
            pass
    for attempt in range(max_retries):
        conn = None
        try:
            conn = get_db_fresh(use_dict_cursor=True)
            if conn.closed:
                raise psycopg2.OperationalError("Connection is closed")
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
                    cur.close()
                    return [], None
                latest = row['max_date']
                # 2026-10-14 审查：回看 7 天拿到的可能不是当日数据，必须显式提示
                if latest < ref_date:
                    print(f"  ⚠ load_candidates({source}): 最新数据 {latest} "
                          f"落后 {(ref_date - latest).days} 天（回看窗口 7 天）")

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
            cur.close()
            return candidates, latest
        except Exception as e:
            if ("SSL connection" in str(e) or "closed" in str(e).lower()) and attempt < max_retries - 1:
                delay = 3 * (attempt + 1)
                print(f"  ⚠ load_candidates({source}) SSL error, retrying in {delay}s ({attempt+1}/{max_retries})...")
                _t.sleep(delay)
                continue
            raise
        finally:
            _close_conn(conn)


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


def load_strategy_timestamp(source, snapshot_date=None):
    """Load the exact created_at timestamp for a strategy's latest data.
    
    PostgreSQL TIMESTAMPTZ 返回 UTC 时间，需转换为北京时间 (UTC+8) 显示。
    """
    if source == 'funnel':
        if snapshot_date:
            row = query_one("""
                SELECT created_at FROM funnel_results WHERE trade_date = %s;
            """, (snapshot_date,))
        else:
            row = query_one("""
                SELECT created_at FROM funnel_results ORDER BY trade_date DESC LIMIT 1;
            """)
    else:
        if snapshot_date:
            row = query_one("""
                SELECT created_at FROM strategy_scans
                WHERE strategy = %s AND snapshot_date = %s;
            """, (source, snapshot_date))
        else:
            row = query_one("""
                SELECT created_at FROM strategy_scans
                WHERE strategy = %s ORDER BY snapshot_date DESC LIMIT 1;
            """, (source,))
    if row and row.get('created_at'):
        ct = row['created_at']
        if hasattr(ct, 'astimezone'):
            ct = ct.astimezone(BEIJING_TZ)
        elif hasattr(ct, 'replace'):
            ct = ct.replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ)
        if hasattr(ct, 'strftime'):
            return ct.strftime('%Y-%m-%d %H:%M:%S')
        return str(ct)
    return None


def load_main_uptrend_backtest():
    """从 docs 目录加载 main_uptrend 回测 JSON 数据"""
    backtest_file = Path(__file__).parent / "docs" / "main_uptrend_backtest.json"
    if not backtest_file.exists():
        return None
    with open(backtest_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return clean_nan(data)


def load_pullback_backtest():
    """从 docs 目录加载 回调买入法 (pullback_buy) 回测 JSON 数据"""
    backtest_file = Path(__file__).parent / "docs" / "pullback_backtest.json"
    if not backtest_file.exists():
        return None
    try:
        with open(backtest_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return clean_nan(data)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠ 加载 pullback_backtest.json 失败: {e}")
        return None


def load_forward_stats(filename):
    """加载 forward_stats.py 输出的三策略前向收益 JSON（2026-10-13 审查新增）"""
    path = Path(__file__).parent / "docs" / filename
    if not path.exists():
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return clean_nan(json.load(f))
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠ 加载 {filename} 失败: {e}")
        return None


def _sig_mark(p):
    """p 值 → 显著性星号。"""
    if p is None:
        return ''
    try:
        p = float(p)
    except (TypeError, ValueError):
        return ''
    if p < 0.01:
        return '**'
    if p < 0.05:
        return '*'
    return ''


def render_fwd_table(fwd, benchmark=None, excess=None, title="前向收益统计",
                     excess_sig=None):
    """统一渲染前向收益表（含 t 值显著性 / 全市场基准 / 超额）。

    fwd: {days: {count, win_rate, mean, median, t, p, n_eff, nonoverlap{...}}}
    excess_sig: {days: cluster_t_stat(...) 按信号日配对差分的超额显著性}
    """
    if not fwd:
        return ""
    rows = ""
    for days_key in ['10', '20', '60']:
        fd = fwd.get(days_key) or fwd.get(int(days_key)) or {}
        if not fd:
            continue
        wr = _fnum(fd.get('win_rate'))
        n = int(_fnum(fd.get('count')))
        mn = _fnum(fd.get('mean'))
        md = _fnum(fd.get('median'))
        a5 = _fnum(fd.get('above_5pct'))
        a10 = _fnum(fd.get('above_10pct'))
        t_v = fd.get('t')
        p_v = fd.get('p')
        mark = _sig_mark(p_v)
        t_str = f"{t_v:.2f}{mark}" if isinstance(t_v, (int, float)) else '—'
        color = '#3fb950' if wr > 0.55 else ('#f0883e' if wr > 0.50 else '#f85149')
        bm = (benchmark or {}).get(days_key) or (benchmark or {}).get(int(days_key))
        ex = (excess or {}).get(days_key) if excess else None
        if ex is None and bm:
            ex = mn - (bm.get('mean', 0) or 0)
        bm_str = f"{bm['mean']:+.2%}" if bm and bm.get('mean') is not None else '—'
        ex_str = f"{ex:+.2%}" if isinstance(ex, (int, float)) else '—'
        ex_color = '#3fb950' if isinstance(ex, (int, float)) and ex > 0 else '#f85149'
        non = fd.get('nonoverlap') or {}
        non_str = (f"n={int(_fnum(non.get('count')))} {_fnum(non.get('mean')):+.2%} "
                   f"t={_fnum(non.get('t')):.2f}{_sig_mark(non.get('p'))}") if non.get('count') else '—'
        rows += f"""
        <tr>
          <td><strong>{days_key}日</strong></td>
          <td>{n}</td>
          <td style="color:{color}"><strong>{wr:.1%}</strong></td>
          <td>{mn:+.2%}</td>
          <td>{md:+.2%}</td>
          <td>{a5:.1%}</td>
          <td>{a10:.1%}</td>
          <td>{t_str}</td>
          <td>{bm_str}</td>
          <td style="color:{ex_color}">{ex_str}</td>
          <td>{non_str}</td>
        </tr>"""
    if not rows:
        return ""
    # 脚注：直接复用 shared.backtest_stats.SIGNIFICANCE_NOTE（2026-10-14 审查），
    # 其中已含"多重比较未校正、参数与评估同区间 p 值偏乐观"提示
    sig_note = html_escape(SIGNIFICANCE_NOTE)
    ex_sig_html = ""
    if excess_sig:
        ex_parts = []
        for _dk in ('10', '20', '60'):
            _es = excess_sig.get(_dk) or excess_sig.get(int(_dk))
            if not _es or not isinstance(_es.get('t'), (int, float)):
                continue
            _mk = '（低功效）' if _es.get('low_power') else _sig_mark(_es.get('p'))
            ex_parts.append(f"{_dk}日 t={_es['t']:.2f}{_mk}")
        if ex_parts:
            ex_sig_html = (
                '<div style="font-size:.62rem;color:var(--text-secondary);margin-top:2px;">'
                '超额显著性（按信号日配对差分·按日聚类 t）：'
                + ' · '.join(ex_parts) + '</div>')
    return f"""
    <div style="overflow-x:auto;margin:8px 0;">
    <div style="font-size:.7rem;color:var(--text-secondary);margin-bottom:4px;">{title}</div>
    <table style="width:100%;font-size:.68rem;border-collapse:collapse;white-space:nowrap;">
    <thead><tr style="background:var(--metric-bg)">
      <th>持有期</th><th>样本</th><th>胜率</th><th>均值</th><th>中位数</th>
      <th>&gt;5%</th><th>&gt;10%</th><th>t值</th><th>基准</th><th>超额</th><th>非重叠子样本</th>
    </tr></thead>
    <tbody>{rows}</tbody>
    </table>
    <div style="font-size:.62rem;color:var(--text-secondary);margin-top:4px;">
      {html_escape(COST_NOTE)} · 基准=全市场等权同窗口 · 非重叠=窗口不重叠的独立子样本
      （价格为前复权价）<br>{sig_note}
    </div>
    {ex_sig_html}
    </div>"""


def render_by_year(by_year):
    """分子期稳定性小表。"""
    if not by_year:
        return ""
    rows = ""
    for year in sorted(by_year.keys()):
        yr = by_year[year] or {}
        rows += (f"<tr><td>{year}</td><td>{int(_fnum(yr.get('count')))}</td>"
                 f"<td>{_fnum(yr.get('win_rate')):.1%}</td>"
                 f"<td>{_fnum(yr.get('mean')):+.2%}</td>"
                 f"<td>{_fnum(yr.get('fwd10_mean')):+.2%}</td>"
                 f"<td>{_fnum(yr.get('fwd10_win')):.1%}</td></tr>")
    return f"""
    <div style="overflow-x:auto;margin:8px 0;">
    <div style="font-size:.7rem;color:var(--text-secondary);margin-bottom:4px;">📅 分子期稳定性（检验结果是否由单段行情主导）</div>
    <table style="width:100%;font-size:.68rem;border-collapse:collapse;white-space:nowrap;">
    <thead><tr style="background:var(--metric-bg)">
      <th>年份</th><th>样本</th><th>胜率</th><th>出场均值</th><th>前向10日均值</th><th>前向10日胜率</th>
    </tr></thead>
    <tbody>{rows}</tbody>
    </table>
    </div>"""


def load_main_uptrend_run_stats(trade_date=None):
    """从 main_uptrend_runs 加载当日运行统计和运行时间"""
    try:
        if trade_date:
            row = query_one("""
                SELECT run_date, a_pool_size, b_signals, c_signals, d_passed, candidates, details, created_at
                FROM main_uptrend_runs WHERE run_date = %s;
            """, (trade_date,))
        else:
            row = query_one("""
                SELECT run_date, a_pool_size, b_signals, c_signals, d_passed, candidates, details, created_at
                FROM main_uptrend_runs ORDER BY run_date DESC LIMIT 1;
            """)
        if row:
            result = dict(row)
            ct = result.get('created_at')
            if ct and hasattr(ct, 'astimezone'):
                ct = ct.astimezone(BEIJING_TZ)
            elif ct and hasattr(ct, 'replace'):
                ct = ct.replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ)
            if ct and hasattr(ct, 'strftime'):
                result['run_timestamp'] = ct.strftime('%Y-%m-%d %H:%M:%S')
            else:
                result['run_timestamp'] = str(ct) if ct else None
            return result
    except Exception as e:
        print(f"  ⚠ 加载 main_uptrend run stats 失败: {e}")
    return None


def load_main_uptrend_daily(trade_date=None):
    """从 daily_candidates 加载 main_uptrend 当日候选，补充 stock_name"""
    try:
        candidates, date = load_candidates('main_uptrend', trade_date=trade_date)
        if candidates:
            codes = [c.get('ts_code', '') for c in candidates if not c.get('stock_name')]
            if codes:
                try:
                    conn = get_db_fresh(use_dict_cursor=True)
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT ts_code, stock_name FROM stock_basic_info WHERE ts_code = ANY(%s);", (codes,))
                        name_map = {r['ts_code']: r['stock_name'] for r in cur.fetchall()}
                        cur.close()
                    finally:
                        _close_conn(conn)
                    for c in candidates:
                        if not c.get('stock_name'):
                            c['stock_name'] = name_map.get(c.get('ts_code', ''), '')
                except Exception:
                    pass
        return candidates, date
    except Exception as e:
        print(f"  ⚠ 加载 main_uptrend daily 数据失败: {e}")
        return [], None


def generate_unified_html(output_dir=None, trade_date=None):
    if output_dir is None:
        output_dir = Path(__file__).parent / "docs"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 加载五策略数据 ──
    funnel = load_funnel_data(trade_date)
    funnel_date = str(funnel['trade_date']) if funnel else None
    funnel_timestamp = load_strategy_timestamp('funnel', funnel_date)

    llm_candidates, llm_date = load_candidates('llm_multisource', trade_date=trade_date, run_mode='afternoon')
    if not llm_candidates:
        llm_candidates, llm_date = load_candidates('llm_multisource', trade_date=trade_date)
    llm_timestamp = load_strategy_timestamp('llm_multisource', llm_date)

    eight_candidates, eight_date = load_candidates('overnight_8step', trade_date=trade_date)
    # 并行 workflow 竞争：overnight_8step 和 funnel 同时 15:10 触发，
    # zuiyou1.py 可能还在写入，最多重试 8 次（每次 15s，共 120s）
    # 优化：生成历史日期报告时跳过重试（历史数据不会变化），仅当天日期重试
    target_date = trade_date if trade_date else str(get_beijing_date())
    # 统一转换为字符串比较，避免 date 对象和字符串类型不匹配
    target_date_str = str(target_date)
    eight_date_str_for_check = str(eight_date) if eight_date else None
    
    is_today = (target_date_str == str(get_beijing_date()))
    need_retry = (not eight_candidates or not eight_date_str_for_check) or (eight_date_str_for_check and eight_date_str_for_check < target_date_str)
    if need_retry and is_today:
        # 仅当天日期重试（等待并行 workflow 写入）
        import time as _time
        for _retry in range(8):
            _time.sleep(15)
            print(f"   overnight_8step 数据未就绪 (当前={eight_date}, 期望>={target_date_str})，15s后重试 ({_retry+1}/8)...")
            eight_candidates, eight_date = load_candidates('overnight_8step', trade_date=trade_date)
            eight_date_str_for_check = str(eight_date) if eight_date else None
            if eight_candidates and eight_date_str_for_check and eight_date_str_for_check >= target_date_str:
                break
        # 如果重试后仍然没有目标日期的数据，使用最新可用数据
        eight_date_str_for_check = str(eight_date) if eight_date else None
        if not eight_candidates or not eight_date_str_for_check or eight_date_str_for_check < target_date_str:
            print(f"   ⚠ overnight_8step {target_date_str} 无数据，使用最新可用数据 (日期={eight_date})")
    elif need_retry and not is_today:
        # 历史日期不重试，直接使用已有数据
        print(f"   ⚠ overnight_8step {target_date_str} 无数据，使用最新可用数据 (日期={eight_date})")
    # 报告日期必须严格匹配：没有当日候选时展示 0，而不是把上一有效日的票挂到报告日。
    eight_timestamp = None
    if str(eight_date or "") < target_date_str:
        print(f"   ⚠ overnight_8step {target_date_str} 无当日数据，按 0 候选展示")
        eight_candidates = []
        eight_date = target_date_str
        eight_timestamp = f"{target_date_str} 15:10:00"
        eight_scan = None
    eight_date_str = str(eight_date) if eight_date else None
    eight_timestamp = load_strategy_timestamp('overnight_8step', eight_date) or eight_timestamp

    llm_scan = load_scan_stats('llm_multisource', llm_date)
    eight_scan = load_scan_stats('overnight_8step', eight_date)

    uptrend_daily, uptrend_date = load_main_uptrend_daily(trade_date=trade_date)
    uptrend_backtest = load_main_uptrend_backtest()
    uptrend_run_stats = load_main_uptrend_run_stats(trade_date=trade_date)

    # ── 回调买入法 (pullback_buy) ──
    pb_candidates, pb_date = load_candidates('pullback_buy', trade_date=trade_date)
    pb_scan = load_scan_stats('pullback_buy', pb_date)
    pb_backtest = load_pullback_backtest()
    pb_timestamp = load_strategy_timestamp('pullback_buy', pb_date) if (pb_date or pb_scan) else None

    # ── 三策略前向收益重算（2026-10-13 审查新增，forward_stats.py 产出）──
    funnel_fwd = load_forward_stats("funnel_forward.json")
    llm_fwd = load_forward_stats("llm_forward.json")
    eight_fwd = load_forward_stats("eight_forward.json")

    def _fwd_block(fwd_json, title="📈 历史候选前向收益（重算口径）"):
        """把 forward_stats JSON 渲染成策略区块内的小节。"""
        if not fwd_json or not fwd_json.get("forward"):
            return ""
        rng = fwd_json.get("sample_range", ["—", "—"])
        total = fwd_json.get("total_candidates", 0)
        head = (f'<div style="font-size:.7rem;color:var(--text-secondary);margin-top:10px;">'
                f'{title}<br>样本 {rng[0]} ~ {rng[1]} · 候选 {total} 只 · '
                f'{fwd_json.get("entry_convention", "")}'
                f'{fwd_generated_note(fwd_json)}</div>')
        extra = ""
        decomp = fwd_json.get("overnight_decomp")
        if decomp:
            o, i = decomp.get("overnight", {}), decomp.get("intraday", {})
            extra = (f'<div style="font-size:.66rem;color:var(--text-secondary);margin-top:4px;">'
                     f'隔夜段 {o.get("mean", 0):+.2%} t={o.get("t", 0):.2f} · '
                     f'日内段 {i.get("mean", 0):+.2%} t={i.get("t", 0):.2f}'
                     f'（验证"A股隔夜收益为负"文献结论）</div>')
        excl = fwd_json.get("excluded_morning_reuse", 0) or 0
        if excl:
            extra += (f'<div style="font-size:.64rem;color:var(--text-secondary);margin-top:2px;">'
                      f'已剔除 {excl} 条盘前回退复用信号（morning_reuse），'
                      f'避免虚增样本量</div>')
        dropped = fwd_json.get("dropped_by_reason") or {}
        if dropped:
            d10 = dropped.get("10") or {}
            if d10:
                extra += (f'<div style="font-size:.62rem;color:var(--text-secondary);margin-top:2px;">'
                          f'10日口径剔除样本：超范围 {d10.get("out_of_range", 0)} · '
                          f'无入场价 {d10.get("no_entry", 0)} · '
                          f'无出场价 {d10.get("no_exit", 0)}</div>')
        return head + render_fwd_table(
            fwd_json.get("forward"), fwd_json.get("benchmark"),
            fwd_json.get("excess"),
            excess_sig=fwd_json.get("excess_sig")) + extra

    funnel_fwd_html = _fwd_block(funnel_fwd)
    llm_fwd_html = _fwd_block(llm_fwd)
    eight_fwd_html = _fwd_block(eight_fwd)

    display_date = str(funnel_date or llm_date or eight_date or get_beijing_date())

    # ── 候选数据滞后提示（load_candidates 只回看 7 天，可能不是当日数据）──
    funnel_stale_html = stale_data_note(funnel_date, "漏斗候选")
    llm_stale_html = stale_data_note(llm_date, "LLM多源候选")
    eight_stale_html = stale_data_note(eight_date, "八步法候选")
    pb_stale_html = stale_data_note(pb_date, "回调买入候选")
    uptrend_stale_html = stale_data_note(uptrend_date, "主升浪候选")

    # ── 漏斗步骤（文案全部由 DEFAULT_FUNNEL_CONFIG 动态生成，2026-10-14 审查）──
    LAYER_RULES = build_layer_rules()
    SIGNAL_MAP = {
        'pullback_bounce': '回踩反弹',
        'trend_continuation': '趋势延续',
        'demand_absorption': '需求吸收',
        'strong_relay': '强势接力',
        'none': '无信号',
    }

    funnel_steps_html = ""
    if funnel:
        total = funnel['total_stocks']
        if total == 0:
            # 判断是数据未就绪还是大盘风控未通过
            l0 = funnel.get('layer0_pass', 0)
            if l0 == 0:
                funnel_steps_html = '<div class="no-data">L0 大盘风控未通过<br><small>（上涨家数不足或市场广度低于20EMA，当日不荐股）</small></div>'
            else:
                funnel_steps_html = '<div class="no-data">漏斗策略运行时行情数据未就绪，各层统计为空<br><small>（daily_quotes 在收盘后才写入，漏斗策略需在数据就绪后运行）</small></div>'
        else:
            steps_data = [
                ("全市场", "📊", total, 0, "A股全市场日成交额>1亿", "#e3f2fd"),
                ("L0 大盘风控", "🌤️", funnel['layer0_pass'], 0,
                 ("✅满仓" if funnel['layer0_pass'] else "⚠️限仓") + " · " + LAYER_RULES['L0'],
                 "#fff3e0"),
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
                rule_html = (f'<span class="rule-inline">{html_escape(rule)}</span>'
                             if rule else '')
                title_attr = f' title="{html_escape(rule)}"' if rule else ''
                funnel_steps_html += f"""
            <div class="step-row"{title_attr}>
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
            if isinstance(v, int):
                reject_map[k] = reject_map.get(k, 0) + v

        sentiment = eight_scan.get('sentiment_score')
        mood = eight_scan.get('mood', '')
        mood_text = f"情绪{mood}({sentiment}分)" if sentiment else ""

        step_rules = {
            "涨幅筛选": "稳健3%~5%，高位6%~9.7%，剔除涨停/跌停",
            "成交额过滤": "稳健0.5~50亿，高位0.3~30亿，保证流动性",
            "换手率过滤": "5%~10%，活跃度适中",
            "市值过滤": "稳健50~500亿，高位30~200亿",
            "量比过滤": "量比≥1，放量确认",
            "均线+压力": "MA多头排列 + 无上方压力位",
            "乖离率过滤": "乖离率≤5%，避免追高",
            "综合评分": "综合评分≥阈值(85+)，量化+情绪加权",
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
            title_attr = f' title="{rule}"' if rule else ''
            eight_steps_html += f"""
            <div class="step-row"{title_attr}>
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
            "多源信号采集": "6大信号源聚合：龙虎榜/涨停板/个股研报/机构调研/盈利预测/巨潮公告，提取结构化推荐",
            "流动性过滤": "成交额>1亿(涨停票>1亿/研报票>5000万)；剔除ST/次新股(上市<60天)；盘后模式剔除已涨停股",
            "量化评分": "9维量化打分：涨幅(-3%~7%最优)/换手率/成交额/振幅/量比/委比/大单净量(降权50%)/强势股排名/机构预期，满分100（主力资金维度已剔除：无独立预测力，2026-10-13审查）",
            "估值与财务": "PE/PB估值打分(PE<15加分/PE>60扣分)；净利润率/毛利率/负债率/现金流质量评估",
            "综合评分≥阈值": "量化评分+LLM评分(多源强度×置信度加权)≥60分进入候选池",
            "精选标记": "综合评分≥25分且多源交叉验证(≥2个独立信号源)标记为精选，最多取5只",
        }
        llm_steps_html += f"""
        <div class="step-row" title="{llm_step_rules['多源信号采集']}">
          <span class="step-name">多源信号采集</span>
          <span class="step-pass">{stats.get('多源聚合后', '—')}</span>
          <span class="rule-inline">{llm_step_rules['多源信号采集']}</span>
        </div>
        <div class="step-row" title="{llm_step_rules['流动性过滤']}">
          <span class="step-name">流动性过滤</span>
          <span class="rule-inline">{llm_step_rules['流动性过滤']}</span>
        </div>
        <div class="step-row" title="{llm_step_rules['量化评分']}">
          <span class="step-name">量化评分</span>
          <span class="rule-inline">{llm_step_rules['量化评分']}</span>
        </div>
        <div class="step-row" title="{llm_step_rules['估值与财务']}">
          <span class="step-name">估值与财务</span>
          <span class="rule-inline">{llm_step_rules['估值与财务']}</span>
        </div>
        <div class="step-row" title="{llm_step_rules['综合评分≥阈值']}">
          <span class="step-name">综合评分≥阈值</span>
          <span class="step-pass">{stats.get('综合评分≥阈值', '—')}</span>
          <span class="rule-inline">{llm_step_rules['综合评分≥阈值']}</span>
        </div>
        <div class="step-row" title="{llm_step_rules['精选标记']}">
          <span class="step-name">精选标记</span>
          <span class="step-pass">{stats.get('精选标记', '—')}</span>
          <span class="rule-inline">{llm_step_rules['精选标记']}</span>
        </div>"""
    else:
        llm_steps_html = """
        <div class="step-row" title="6大信号源聚合：龙虎榜/涨停板/个股研报/机构调研/盈利预测/巨潮公告，提取结构化推荐">
          <span class="step-name">多源信号采集</span>
          <span class="rule-inline">6大信号源聚合：龙虎榜/涨停板/个股研报/机构调研/盈利预测/巨潮公告，提取结构化推荐</span>
        </div>
        <div class="step-row" title="成交额>1亿(涨停票>1亿/研报票>5000万)；剔除ST/次新股(上市<60天)；盘后模式剔除已涨停股">
          <span class="step-name">流动性过滤</span>
          <span class="rule-inline">成交额>1亿(涨停票>1亿/研报票>5000万)；剔除ST/次新股(上市<60天)；盘后模式剔除已涨停股</span>
        </div>
        <div class="step-row" title="9维量化打分：涨幅/换手率/成交额/振幅/量比/委比/大单净量(降权50%)/强势股排名/机构预期，满分100（主力资金维度已剔除）">
          <span class="step-name">量化评分</span>
          <span class="rule-inline">9维量化打分：涨幅/换手率/成交额/振幅/量比/委比/大单净量(降权50%)/强势股排名/机构预期，满分100（主力资金维度已剔除）</span>
        </div>
        <div class="step-row" title="PE/PB估值打分；净利润率/毛利率/负债率/现金流质量评估">
          <span class="step-name">估值与财务</span>
          <span class="rule-inline">PE/PB估值打分；净利润率/毛利率/负债率/现金流质量评估</span>
        </div>
        <div class="step-row" title="量化评分+LLM评分(多源强度×置信度加权)≥60分进入候选池">
          <span class="step-name">综合评分≥阈值</span>
          <span class="rule-inline">量化评分+LLM评分(多源强度×置信度加权)≥60分进入候选池</span>
        </div>
        <div class="step-row" title="综合评分≥25分且多源交叉验证(≥2个独立信号源)标记为精选，最多取5只">
          <span class="step-name">精选标记</span>
          <span class="rule-inline">综合评分≥25分且多源交叉验证(≥2个独立信号源)标记为精选，最多取5只</span>
        </div>"""

    # ── 主升浪步骤与回测展示 ──
    uptrend_steps_html = ""
    top10_html = ""

    # 优先显示运行统计（A/B/C/D 层结果 + 运行时间）
    run_stats_parts = []
    if uptrend_run_stats:
        rs = uptrend_run_stats
        run_time = rs.get('run_timestamp', '—')
        run_stats_parts.append(f"""
        <div class="step-row">
          <span class="step-name">运行时间</span>
          <span class="step-pass">{run_time}</span>
        </div>
        <div class="step-row">
          <span class="step-name">A 层预筛池</span>
          <span class="step-pass">{rs.get('a_pool_size', 0)} 只</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">A1 业绩加速</span>
          <span class="rule-inline">归母净利润同比 &gt; 30%</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">A2 市值适中</span>
          <span class="rule-inline">流通市值 50-200 亿</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">A3 行业景气</span>
          <span class="rule-inline">申万二级行业近60日涨幅排名前50%（2026-10-13: 形成期20→60日，避开A股反转区间）</span>
        </div>
        <div class="step-row">
          <span class="step-name">B 层启动信号</span>
          <span class="step-pass">{rs.get('b_signals', 0)} 只</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">B1 量能突破</span>
          <span class="rule-inline">量比 &gt; 1.5 且换手率 &gt; 3%</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">B2 价格突破</span>
          <span class="rule-inline">突破60日箱体高点 &gt; 1% 或刚站上120日均线</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">B3 主力资金</span>
          <span class="rule-inline">已降级为弱加权项，不再一票否决（主力净流入无独立预测力，2026-10-13审查）</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">B4 封单质量</span>
          <span class="rule-inline">涨停封单额/流通市值 &gt; 0.5% 或涨幅 &gt; 3%</span>
        </div>
        <div class="step-row">
          <span class="step-name">C 层持续性</span>
          <span class="step-pass">{rs.get('c_signals', 0)} 只</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">C1 分时形态</span>
          <span class="rule-inline">当日上涨即可（回测模式）</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">C2 大单买入</span>
          <span class="rule-inline">成交额/20日均 &gt; 1.0 且当日上涨</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">C3 缩量上涨</span>
          <span class="rule-inline">量比 0.2~1.5 且当日上涨</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">C4 板上量比</span>
          <span class="rule-inline">涨停或涨幅 &gt; 3%</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">C5 板块联动</span>
          <span class="rule-inline">行业平均涨幅 &gt; 0% 且至少1只同涨</span>
        </div>
        <div class="step-row">
          <span class="step-name">D 层通过</span>
          <span class="step-pass">{rs.get('d_passed', 0)} 只</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">D1 ST/退市</span>
          <span class="rule-inline">剔除ST、*ST、退市股</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">D2 减持</span>
          <span class="rule-inline">近30日内无重大减持公告</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">D3 诱多</span>
          <span class="rule-inline">量比 &gt; 5 但封单不足流通市值0.3%剔除</span>
        </div>
        <div class="step-row" style="padding-left:16px;font-size:.65rem;color:var(--text-secondary);">
          <span class="step-name" style="font-size:.6rem;">D4 高质押</span>
          <span class="rule-inline">大股东质押 &gt; 50% 且连板 &gt; 3 剔除</span>
        </div>
        <div class="step-row">
          <span class="step-name">最终候选</span>
          <span class="step-pass">{rs.get('candidates', 0)} 只</span>
        </div>""")
    elif uptrend_daily:
        run_stats_parts.append("""
        <div class="step-row">
          <span class="step-name">今日运行</span>
          <span class="step-pass">✓</span>
        </div>""")

    if uptrend_backtest:
        bt = uptrend_backtest
        signal_count = bt.get('total_signals', 0)
        period = f"{bt.get('backtest_start', '')} ~ {bt.get('backtest_end', '')}"
        fwd = bt.get('forward_returns', {})

        fwd_table_html = render_fwd_table(
            fwd, bt.get('benchmark'), bt.get('excess'))

        backtest_html = f"""
        <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border);">
        <div style="font-size:.75rem;color:var(--text-secondary);margin-bottom:6px;">📊 回测统计</div>
        <div class="step-row">
          <span class="step-name">回测区间</span>
          <span class="rule-inline">{period}</span>
        </div>
        <div class="step-row">
          <span class="step-name">总信号数</span>
          <span class="step-pass">{signal_count}</span>
        </div>
        {fwd_table_html}
        {render_by_year(bt.get('by_year'))}
        </div>"""

        top10_html = ""
        for s in bt.get('top10_signals', [])[:5]:
            frets = s.get('forward_rets', {}) or {}
            ret_10 = f"{_fnum(frets.get('10')):.1%}" if '10' in frets else '—'
            ret_20 = f"{_fnum(frets.get('20')):.1%}" if '20' in frets else '—'
            ret_60 = f"{_fnum(frets.get('60')):.1%}" if '60' in frets else '—'
            _ts_code = str(s.get('ts_code', '') or '')
            _name = html_escape(str(s.get('stock_name', '') or ''))
            _score = _fnum(s.get('composite_score'))
            _eval = s.get('eval_date', '')
            _signal = s.get('signal_type', '')
            _signal_map = {'pullback_bounce': '回踩反弹', 'trend_continuation': '趋势延续', 'demand_absorption': '需求吸收', 'strong_relay': '强势接力'}
            _signal_label = html_escape(str(_signal_map.get(_signal, _signal) or ''))
            _tags = []
            if _signal_label:
                _tags.append(_signal_label)
            _win = _fnum(frets.get('win_rate'))
            if _win > 0:
                _tags.append(f'胜率{_win:.0%}')
            _tags_str = ' · '.join(_tags) if _tags else ''
            _stock_url = ''
            if _ts_code:
                _parts = _ts_code.split('.')
                if len(_parts) == 2:
                    _stock_url = f'https://finance.baidu.com/stock/ab-{_parts[0]}'
            _onclick = f' onclick="window.open(\'{_stock_url}\',\'_blank\')" style="cursor:pointer"' if _stock_url else ''
            _code_link = f'<a href="{_stock_url}" target="_blank" style="color:inherit;text-decoration:none">{_ts_code}</a>' if _stock_url else _ts_code
            top10_html += f"""
            <div class="candidate-card"{_onclick}>
              <div class="cand-header">
                <div>
                  <span class="cand-code">{_code_link}</span>
                  <span class="cand-name">{_name or ''}</span>
                </div>
                <span class="cand-score">{_score:.0f}</span>
              </div>
              <div class="cand-metrics">
                <div class="metric"><span class="mv">{ret_10}</span><span class="ml">10日</span></div>
                <div class="metric"><span class="mv">{ret_20}</span><span class="ml">20日</span></div>
                <div class="metric"><span class="mv">{ret_60}</span><span class="ml">60日</span></div>
              </div>
              {f'<div class="cand-tags">{_tags_str}</div>' if _tags_str else ''}
            </div>"""

        uptrend_steps_html = "".join(run_stats_parts) + backtest_html if run_stats_parts else backtest_html
    elif run_stats_parts:
        uptrend_steps_html = "".join(run_stats_parts)
    else:
        uptrend_steps_html = '<div class="no-data">暂无数据<br><small>运行主升浪检测后自动展示</small></div>'

    # ── 回调买入法（pullback_buy）步骤与回测展示 ──
    pb_step_defs = [
        ('流动性/风控', '成交额≥0.5亿 · 股价≥3元 · 非ST · 上市≥60天 · 当日未涨停(按板块规则)'),
        ('强势股筛选', '120日动能居当日截面前30% · EMA9>EMA21>EMA50 多头 · 收盘>EMA50'),
        ('回调至支撑', '自20日高点回撤5%~25% · 低点触及EMA21/EMA50(±3%) · 回调段缩量'),
        ('回升确认', '收阳 · 收盘>前日收盘 · 重新站上EMA21'),
        ('波动率止损达标', '止损=min(结构止损, 1.5×ATR14) · 风险≤6% · 目标=入场+2×风险'),
    ]
    pb_stats = pb_scan.get('filter_stats', {}) if pb_scan else {}
    pb_parts = []
    if pb_stats:
        pb_parts.append(f"""
        <div class="step-row">
          <span class="step-name">扫描日期</span>
          <span class="rule-inline">{pb_scan.get('snapshot_date', '—')}</span>
        </div>""")
        for _name, _rule in pb_step_defs:
            _cnt = pb_stats.get(_name, '—')
            pb_parts.append(f"""
        <div class="step-row" title="{_rule}">
          <span class="step-name">{_name}</span>
          <span class="step-pass">{_cnt}</span>
          <span class="rule-inline">{_rule}</span>
        </div>""")

    pb_backtest_html = ""
    pb_top10_html = ""
    if pb_backtest:
        bt = pb_backtest
        es = bt.get('exit_stats', {})
        period = f"{bt.get('backtest_start', '')} ~ {bt.get('backtest_end', '')}"
        fwd = bt.get('forward_returns', {})

        fwd_table_html = render_fwd_table(
            fwd, bt.get('benchmark'), bt.get('excess'))
        exit_t = es.get('t')
        exit_sig = (f" · t={exit_t:.2f}{_sig_mark(es.get('p'))}"
                    if isinstance(exit_t, (int, float)) else "")

        pb_backtest_html = f"""
        <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border);">
        <div style="font-size:.75rem;color:var(--text-secondary);margin-bottom:6px;">📊 回测统计（T+1开盘入场 · 含止损/止盈模拟）</div>
        <div class="step-row">
          <span class="step-name">回测区间</span>
          <span class="rule-inline">{period}</span>
        </div>
        <div class="step-row">
          <span class="step-name">成交笔数</span>
          <span class="step-pass">{bt.get('total_signals', 0)}</span>
          <span class="rule-inline">胜率{es.get('win_rate', 0):.1%} · 均值{es.get('mean', 0):+.1%}{exit_sig} · 持有{es.get('avg_hold_days', 0):.1f}日</span>
        </div>
        <div class="step-row">
          <span class="step-name">出场分布</span>
          <span class="rule-inline">止损 {es.get('stop_count', 0)} · 止盈 {es.get('target_count', 0)} · 超时 {es.get('timeout_count', 0)}{(' · 时间止损 ' + str(es.get('time_stop_count', 0))) if es.get('time_stop_count', 0) else ''}</span>
        </div>
        {fwd_table_html}
        {render_by_year(bt.get('by_year'))}
        </div>"""

        # 回测 Top5 作为无当日候选时的兜底卡片
        for s in bt.get('top10_signals', [])[:5]:
            frets = s.get('forward_rets', {}) or {}
            ret_10 = f"{_fnum(frets.get('10')):.1%}" if '10' in frets else '—'
            ret_20 = f"{_fnum(frets.get('20')):.1%}" if '20' in frets else '—'
            ret_60 = f"{_fnum(frets.get('60')):.1%}" if '60' in frets else '—'
            _ts_code = str(s.get('ts_code', '') or '')
            _name = html_escape(str(s.get('stock_name', '') or ''))
            _score = _fnum(s.get('composite_score'))
            _exit_ret = _fnum(s.get('exit_ret'))
            _exit_map = {'stop': '止损', 'target': '止盈', 'timeout': '超时',
                         'time_stop': '时间止损', 'data_end': '数据中断'}
            _exit_label = html_escape(str(s.get('exit_type', '') or ''))
            _tags_str = (f"{s.get('eval_date', '')} · "
                         f"{_exit_map.get(_exit_label, _exit_label)} {_exit_ret:+.1%}")
            _stock_url = ''
            if _ts_code:
                _parts = _ts_code.split('.')
                if len(_parts) == 2:
                    _stock_url = f'https://finance.baidu.com/stock/ab-{_parts[0]}'
            _onclick = f' onclick="window.open(\'{_stock_url}\',\'_blank\')" style="cursor:pointer"' if _stock_url else ''
            _code_link = f'<a href="{_stock_url}" target="_blank" style="color:inherit;text-decoration:none">{_ts_code}</a>' if _stock_url else _ts_code
            pb_top10_html += f"""
            <div class="candidate-card"{_onclick}>
              <div class="cand-header">
                <div>
                  <span class="cand-code">{_code_link}</span>
                  <span class="cand-name">{_name or ''}</span>
                </div>
                <span class="cand-score">{_score:.0f}</span>
              </div>
              <div class="cand-metrics">
                <div class="metric"><span class="mv">{ret_10}</span><span class="ml">10日</span></div>
                <div class="metric"><span class="mv">{ret_20}</span><span class="ml">20日</span></div>
                <div class="metric"><span class="mv">{ret_60}</span><span class="ml">60日</span></div>
              </div>
              <div class="cand-tags">{_tags_str}</div>
            </div>"""

    if pb_parts or pb_backtest_html:
        pullback_steps_html = "".join(pb_parts) + pb_backtest_html
    else:
        pullback_steps_html = '<div class="no-data">暂无数据<br><small>运行回调买入法后自动展示</small></div>'

    # ── 候选卡片渲染 ──
    def render_candidate_card(c, badge_class='', badge_text=''):
        code = str(c.get('ts_code', '') or '')
        name = html_escape(str(c.get('stock_name', '') or ''))
        # final_score 可能为 NULL（DB 默认可空），必须兜底否则 {score:.0f} TypeError
        raw_score = c.get('final_score')
        if raw_score is None:
            raw_score = c.get('score')
        try:
            score = float(raw_score) if raw_score is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0
        quant = c.get('quant_score') or c.get('score', 0) or 0
        llm_score = c.get('llm_score', 0) or 0
        entry_l = c.get('entry_low') or c.get('entry_price')
        stop_l = c.get('stop_loss')
        target = c.get('target_1') or c.get('target_price')
        tags = c.get('logic_tags') or c.get('tags', [])
        if isinstance(tags, str):
            tags = tags.replace('/', '|').split('|')
        # 标签来自库内文本，转义后再进 HTML（防注入）
        tags = [html_escape(str(t).strip()) for t in tags if t and str(t).strip()][:4]
        selected = c.get('selected', False)
        sel_cls = 'selected' if selected else ''

        src_badge = ''
        source_list = c.get('sources')
        if source_list and isinstance(source_list, list) and len(source_list) > 0:
            src_badge = f'<span class="src-badge">{len(source_list)}源</span>'

        entry_fmt = f'{entry_l:.2f}' if entry_l else '—'
        stop_fmt = f'{stop_l:.2f}' if stop_l else '—'
        target_fmt = f'{target:.2f}' if target else '—'

        # 百度财经个股页面链接
        stock_url = ''
        if code:
            parts = code.split('.')
            if len(parts) == 2:
                numeric_code = parts[0]
                stock_url = f'https://finance.baidu.com/stock/ab-{numeric_code}'

        onclick_attr = f' onclick="window.open(\'{stock_url}\',\'_blank\')" style="cursor:pointer"' if stock_url else ''
        code_link = (f'<a href="{stock_url}" target="_blank" '
                     f'style="color:inherit;text-decoration:none">{html_escape(code)}</a>'
                     if stock_url else html_escape(code))

        return f"""
        <div class="candidate-card {sel_cls}"{onclick_attr}>
          <div class="cand-header">
            <div>
              <span class="cand-code">{code_link}</span>
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
            signal_text = html_escape(str(SIGNAL_MAP.get(signal, signal) or ''))
            llm_bonus = c.get('llm_bonus', 0) or 0
            bonus_text = f' <span class="llm-badge">LLM+{llm_bonus}</span>' if llm_bonus > 0 else ''
            card = render_candidate_card(c, badge_text=f'<span class="signal-badge">{signal_text}</span>{bonus_text}')
            funnel_cards += card
    if not funnel_cards:
        l5_pass = funnel.get('layer5_pass', 0) if funnel else 0
        l6_pass = funnel.get('layer6_pass', 0) if funnel else 0
        if l5_pass > 0 and l6_pass == 0:
            funnel_cards = f'<div class="no-data">L5通过{l5_pass}只，L6风控全部未通过<br><small>（盈亏比≥2:1、止损≤8%等刚性条件）</small></div>'
        else:
            funnel_cards = '<div class="no-data">今日无推荐</div>'

    llm_cards = ""
    if llm_candidates:
        for c in llm_candidates[:5]:
            card = render_candidate_card(c)
            llm_cards += card
    if not llm_cards:
        llm_cards = '<div class="no-data">今日无推荐</div>'

    eight_cards = ""
    stable_cards = ""
    upper_cards = ""
    if eight_candidates:
        # 去重：同一 ts_code 可能同时出现在稳健和高位路径，保留评分更高的
        seen_codes = {}
        for c in eight_candidates:
            code = c.get('ts_code', '')
            # final_score 可能为 NULL → 兜底 0，避免 None > float TypeError
            score = c.get('final_score') or 0
            prev = seen_codes.get(code)
            if prev is None or score > (prev.get('final_score') or 0):
                seen_codes[code] = c
        deduped_candidates = list(seen_codes.values())

        stable_cards = ""
        upper_cards = ""
        for c in deduped_candidates:
            pool = ''
            sources_raw = c.get('sources')
            if sources_raw:
                if isinstance(sources_raw, str):
                    try:
                        sources_raw = json.loads(sources_raw)
                    except (json.JSONDecodeError, TypeError):
                        sources_raw = []
                if isinstance(sources_raw, list) and sources_raw:
                    pool = sources_raw[0].get('pool', '')
            if not pool:
                tags = c.get('logic_tags', [])
                if isinstance(tags, str):
                    tags = tags.replace('/', '|').split('|')
                for t in (tags or []):
                    if 'pool:stable' in t or 'pool:upper' in t:
                        pool = 'stable' if 'stable' in t else 'upper'
                        break
            if 'stable' in pool.lower():
                stable_cards += render_candidate_card(c)
            elif 'upper' in pool.lower():
                upper_cards += render_candidate_card(c)
            else:
                eight_cards += render_candidate_card(c)
        if stable_cards:
            eight_cards += f'<div class="path-group"><div class="cards-grid">{stable_cards}</div></div>'
        if upper_cards:
            eight_cards += f'<div class="path-group"><div class="path-label path-upper" style="margin-bottom:8px;">高位路径</div><div class="cards-grid">{upper_cards}</div></div>'
    if not eight_cards:
        eight_cards = '<div class="no-data">今日无推荐</div>'

    uptrend_cards = ""
    if uptrend_daily:
        for c in uptrend_daily[:5]:
            card = render_candidate_card(c, badge_text='<span class="badge-8step" style="background:var(--funnel-badge-bg);color:var(--funnel-badge-text)">主升浪</span>')
            uptrend_cards += card
    if not uptrend_cards and uptrend_backtest:
        uptrend_cards = top10_html
    if not uptrend_cards:
        uptrend_cards = '<div class="no-data">无今日候选<br><small>运行每日检测后有实时数据</small></div>'

    pullback_cards = ""
    if pb_candidates:
        for c in pb_candidates[:8]:
            pullback_cards += render_candidate_card(
                c, badge_text='<span class="badge-8step" style="background:var(--funnel-badge-bg);color:var(--funnel-badge-text)">回调买入</span>')
    if not pullback_cards and pb_top10_html:
        pullback_cards = pb_top10_html
    if not pullback_cards:
        pullback_cards = '<div class="no-data">无当日候选<br><small>运行回调买入法后有实时数据</small></div>'

    pullback_count = len(pb_candidates) if pb_candidates else 0

    # ── 历史日期范围（用于 date picker 的 min/max） ──
    # 收集所有策略的日期，确保日期选择器覆盖全部可用日期
    # 注意：所有日期必须统一转换为字符串，否则 sorted() 会报类型错误
    history_dates_raw = query_dicts("""
        SELECT DISTINCT trade_date FROM funnel_results ORDER BY trade_date DESC;
    """)
    hd_set = set()
    for r in history_dates_raw:
        hd_set.add(str(r['trade_date']))
    # 加入漏斗策略日期
    if funnel_date:
        hd_set.add(str(funnel_date))
    # 加入八步法日期
    if eight_date:
        hd_set.add(str(eight_date))
    # 加入LLM策略日期
    if llm_date:
        hd_set.add(str(llm_date))
    # 加入主升浪日期
    if uptrend_date:
        hd_set.add(str(uptrend_date))
    # 查询 daily_candidates 表中所有策略的日期（最全面的日期来源）
    all_candidate_dates = query_dicts("""
        SELECT DISTINCT snapshot_date FROM daily_candidates
        WHERE snapshot_date >= CURRENT_DATE - INTERVAL '90 days'
        ORDER BY snapshot_date DESC;
    """)
    for r in all_candidate_dates:
        hd_set.add(str(r['snapshot_date']))
    # 排序
    hd_list = sorted(hd_set, reverse=True)
    min_date = hd_list[-1] if hd_list else display_date
    max_date = hd_list[0] if hd_list else display_date
    # 将可用日期列表注入到 JS 中，用于提示用户
    available_dates_json = json.dumps(hd_list)

    # ── 汇总统计 ──
    funnel_count = len(funnel['candidates']) if funnel and funnel.get('candidates') else 0
    funnel_elapsed = funnel.get('elapsed_seconds', 0) if funnel else 0
    llm_count = len(llm_candidates)
    eight_count = len(eight_candidates)

    # 八步法 h3 标题行：候选标的 + 稳健路径 合并到一行
    eight_h3 = f'<h3 style="font-size:.9rem;margin:14px 0 8px;color:var(--text);">候选标的 ({eight_count}只)</h3>'
    if stable_cards:
        eight_h3 = f'<h3 style="font-size:.9rem;margin:14px 0 8px;color:var(--text);display:flex;align-items:center;gap:8px;flex-wrap:wrap;">候选标的 ({eight_count}只)<span class="path-label path-stable" style="margin:0;font-size:.78rem;">稳健路径</span></h3>'

    gen_time = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')

    # ════════════════════════════════════════════════════════════
    # HTML 模板
    # ════════════════════════════════════════════════════════════
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>五策略对比 - {display_date}</title>
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
.container {{ max-width:1600px; margin:0 auto; padding:16px; overflow-x:hidden; }}

/* Header */
.header {{ background:var(--header-bg); color:var(--header-text); padding:28px 24px; border-radius:12px; margin-bottom:16px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; }}
.header h1 {{ font-size:1.4rem; margin-bottom:6px; }}
.header .sub {{ font-size:.82rem; opacity:.85; }}
.header .date-input {{ padding:7px 14px; border-radius:6px; border:1px solid rgba(255,255,255,.3); background:rgba(255,255,255,.15); color:var(--header-text); font-size:13px; cursor:pointer; }}
.header .date-input::-webkit-calendar-picker-indicator {{ filter: invert(1); cursor:pointer; }}
.theme-btn {{ background:rgba(255,255,255,.2); color:var(--header-text); border:none; padding:8px 16px; border-radius:20px; cursor:pointer; font-size:14px; transition:background .2s; }}
.theme-btn:hover {{ background:rgba(255,255,255,.3); }}

/* Section */
.section {{ background:var(--section-bg); border-radius:10px; padding:20px; margin-bottom:16px; box-shadow:var(--shadow); border:1px solid var(--border); }}
.section h2 {{ font-size:1.05rem; margin-bottom:14px; padding-bottom:8px; border-bottom:2px solid var(--tab-active); }}
.section-subtitle {{ font-size:.78rem; color:var(--text2); margin-left:8px; font-weight:normal; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}

/* Four column layout */
.four-col {{ display:grid; grid-template-columns:repeat(5,1fr); gap:12px; align-items:stretch; }}
.four-col .section {{ margin-bottom:0; display:flex; flex-direction:column; min-width:0; overflow:hidden; padding:0; }}
.four-col .section > h2 {{ padding:16px 20px 0; margin:0; border-bottom:none; font-size:1.05rem; height:64px; display:flex; align-items:center; flex-wrap:wrap; }}
.four-col .steps-area {{ height:364px; overflow:hidden; padding:0 20px; flex-shrink:0; }}
.four-col .section > h3 {{ margin:0; padding:14px 20px 8px; font-size:.9rem; flex-shrink:0; }}
.four-col .cards-grid {{ flex:1; overflow-y:auto; padding:0 20px 20px; align-content:start; }}
.four-col .candidate-card {{ align-self:stretch; }}
@media (max-width:1400px) {{ .four-col {{ grid-template-columns:repeat(3,1fr); }} }}
@media (max-width:1024px) {{ .four-col {{ grid-template-columns:repeat(2,1fr); }} }}
@media (max-width:768px) {{ .four-col {{ grid-template-columns:1fr; }} }}

/* Keep three-col for backward compatibility */
.three-col {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; align-items:stretch; }}
.three-col .section {{ margin-bottom:0; display:flex; flex-direction:column; }}
.three-col .section > h2 {{ flex-shrink:0; }}
.three-col .section > h3 {{ flex-shrink:0; margin-top:auto; }}
.three-col .cards-grid {{ flex:1; align-content:start; }}
.three-col .candidate-card {{ align-self:stretch; }}
@media (max-width:1024px) {{ .three-col {{ grid-template-columns:1fr; }} }}

/* Funnel steps */
.step-row {{ display:flex; align-items:center; gap:8px; padding:7px 10px; border-radius:6px; font-size:.82rem; transition:background .15s; }}
.step-row:hover {{ background:var(--step-row-hover); }}
.step-icon {{ width:22px; text-align:center; }}
.step-name {{ font-weight:500; min-width:80px; }}
.step-pass {{ font-weight:bold; color:var(--score-color); min-width:36px; text-align:right; }}
.step-row .elim {{ color:var(--elim-color); font-size:.72rem; margin-left:4px; }}
.step-row .rule-inline {{ color:var(--text2); font-size:.7rem; margin-left:auto; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:50%; }}
.step-note {{ font-size:.75rem; color:var(--text2); padding:6px 10px; }}

/* Candidate cards */
.cards-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:10px; }}
.candidate-card {{ background:var(--metric-bg); border-radius:8px; padding:12px; border:1px solid var(--border); transition:transform .15s,box-shadow .15s; min-height:160px; display:flex; flex-direction:column; justify-content:space-between; }}
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
.path-badge {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:.68rem; font-weight:bold; margin-left:4px; }}
.path-stable {{ background:#e8f5e9; color:#2e7d32; }}
.path-upper {{ background:#fff3e0; color:#e65100; }}
.path-group {{ margin-bottom:12px; }}
.path-label {{ font-size:.82rem; font-weight:bold; padding:4px 10px; border-radius:6px; margin-bottom:8px; display:inline-block; }}
.path-label.path-stable {{ background:#e8f5e9; color:#2e7d32; }}
.path-label.path-upper {{ background:#fff3e0; color:#e65100; }}
[data-theme="dark"] .path-stable {{ background:#3fb95033; color:#3fb950; }}
[data-theme="dark"] .path-upper {{ background:#f0883e33; color:#f0883e; }}

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
      <h1>📊 五策略对比选股系统</h1>
      <div class="sub">
        报告日期: {display_date} | 生成: {gen_time}
        <input type="date" class="date-input" id="datePicker" value="{display_date}" min="{min_date}" max="{max_date}" onchange="switchDate(this.value)">
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

  <div class="four-col">
    <!-- ========== 八步法 ========== -->
    <div class="section">
      <h2>🔮 八步隔夜法<span class="section-subtitle">{eight_timestamp or eight_date_str or '—'}</span></h2>
      <div class="steps-area">{eight_steps_html}{eight_fwd_html}</div>
      {eight_h3}
      {eight_stale_html}
      <div class="cards-grid">{eight_cards}</div>
    </div>

    <!-- ========== 七步漏斗 ========== -->
    <div class="section">
      <h2>🎯 七步漏斗选股<span class="section-subtitle">{funnel_timestamp or funnel_date or '—'}</span></h2>
      <div class="steps-area">{funnel_steps_html}{funnel_fwd_html}</div>
      <h3 style="font-size:.9rem;margin:14px 0 8px;color:var(--text);">最终推荐 ({funnel_count}只)</h3>
      {funnel_stale_html}
      <div class="cards-grid">{funnel_cards}</div>
    </div>

    <!-- ========== LLM多源 ========== -->
    <div class="section">
      <h2>🤖 LLM多源策略<span class="section-subtitle">{llm_timestamp or llm_date or '—'}</span></h2>
      <div class="steps-area">{llm_steps_html}{llm_fwd_html}</div>
      <h3 style="font-size:.9rem;margin:14px 0 8px;color:var(--text);">候选标的 ({llm_count}只)</h3>
      {llm_stale_html}
      <div class="cards-grid">{llm_cards}</div>
    </div>

    <!-- ========== 主升浪 ========== -->
    <div class="section">
      <h2>📈 主升浪检测<span class="section-subtitle">{uptrend_run_stats.get('run_timestamp', '') if uptrend_run_stats else (uptrend_backtest.get('backtest_start', '') if uptrend_backtest else (uptrend_date or '—'))}</span></h2>
      <div class="steps-area">{uptrend_steps_html}</div>
      <h3 style="font-size:.9rem;margin:14px 0 8px;color:var(--text);">{('回测 Top 5 (' + str(uptrend_backtest.get('total_signals', 0)) + '只)' if uptrend_backtest and not uptrend_daily else '今日候选') + (' (' + str(len(uptrend_daily)) + '只)' if uptrend_daily else '')}</h3>
      {uptrend_stale_html}
      <div class="cards-grid">{uptrend_cards}</div>
    </div>

    <!-- ========== 回调买入法 ========== -->
    <div class="section">
      <h2>🪝 回调买入法<span class="section-subtitle">{pb_timestamp or (pb_scan.get('snapshot_date', '') if pb_scan else '') or (pb_backtest.get('backtest_start', '') if pb_backtest else '') or '—'}</span></h2>
      <div class="steps-area" style="overflow-y:auto;">{pullback_steps_html}</div>
      <h3 style="font-size:.9rem;margin:14px 0 8px;color:var(--text);">{('今日候选 (' + str(pullback_count) + '只)') if pullback_count else ('回测 Top 5 (' + str(pb_backtest.get('total_signals', 0)) + '笔)' if pb_backtest else '候选标的')}</h3>
      {pb_stale_html}
      <div class="cards-grid">{pullback_cards}</div>
    </div>
  </div>

  <div class="footer">
    AI Stock Recommendation System | 五策略对比 · 每日15:10自动更新
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
  var available = {available_dates_json};
  if (available.indexOf(dateVal) === -1) {{
    // 自动跳转到最近的有数据的日期
    var closest = null;
    var minDiff = Infinity;
    for (var i = 0; i < available.length; i++) {{
      var diff = Math.abs(new Date(available[i]) - new Date(dateVal));
      if (diff < minDiff) {{
        minDiff = diff;
        closest = available[i];
      }}
    }}
    if (closest) {{
      if (confirm('该日期暂无数据\\n\\n是否跳转到最近的有数据日期: ' + closest + '?')) {{
        window.location.href = 'funnel-' + closest + '.html';
      }}
    }} else {{
      alert('暂无可用数据');
    }}
    // 重置日期选择器到当前页面日期
    document.getElementById('datePicker').value = '{display_date}';
    return;
  }}
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
    print(f"  ✓ 五策略对比 HTML: {output_file}")

    if trade_date:
        index_file = output_dir / "index.html"
        index_html = f'''<!DOCTYPE html>
<html><head>
<meta http-equiv="refresh" content="0; url=funnel.html">
<title>五策略对比</title>
</head><body>
<p>跳转到 <a href="funnel.html">五策略对比页面</a></p>
</body></html>'''
        index_file.write_text(index_html, encoding='utf-8')

    return output_file


if __name__ == "__main__":
    import argparse
    import traceback
    parser = argparse.ArgumentParser(description="生成五策略对比 HTML 报告")
    parser.add_argument("--date", "-d", type=str, default=None, help="日期 (YYYY-MM-DD)")
    parser.add_argument("--output", "-o", type=str, default=None, help="输出目录")
    parser.add_argument("--all-dates", action="store_true", help="生成所有历史日期的报告")
    args = parser.parse_args()

    output_dir = args.output
    try:
        if args.all_dates:
            # 收集所有策略的日期（从 daily_candidates 表获取最全面的日期列表）
            all_dates_raw = query_dicts("""
                SELECT DISTINCT snapshot_date FROM daily_candidates
                WHERE snapshot_date >= CURRENT_DATE - INTERVAL '90 days'
                ORDER BY snapshot_date DESC;
            """)
            dates = sorted(set(str(r['snapshot_date']) for r in all_dates_raw), reverse=True)
            print(f"生成 {len(dates)} 个历史日期的报告...")
            for d in dates:
                print(f"  生成 {d}...")
                generate_unified_html(output_dir=output_dir, trade_date=d)
            # 最后生成不带日期的主页面（最新日期）
            print("  生成主页面 funnel.html...")
            generate_unified_html(output_dir=output_dir, trade_date=None)
        else:
            generate_unified_html(output_dir=output_dir, trade_date=args.date)
    except Exception as e:
        print(f"❌ funnel HTML generation failed: {e}")
        traceback.print_exc()
        sys.exit(1)
