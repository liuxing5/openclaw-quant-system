"""生成每日推荐报告 - HTML + 文本格式"""
import os
import json
from datetime import date, timedelta, datetime, timezone
import psycopg2
from psycopg2.extras import RealDictCursor
from jinja2 import Template
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

RUN_MODE = os.getenv('RUN_MODE', 'morning')

# 北京时间时区
BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date():
    """获取北京时间日期（解决 GitHub Actions UTC 时区问题）"""
    return datetime.now(BEIJING_TZ).date()


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
    )


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI 股票推荐 - {{ date }}</title>
    <style>
        :root {
            --bg: #f5f5f5;
            --card-bg: #ffffff;
            --text: #1a1a1a;
            --text-secondary: #666666;
            --border: #e0e0e0;
            --metric-bg: #f5f5f5;
            --entry-bg: #fafafa;
            --header-border: #4CAF50;
            --score-color: #4CAF50;
            --link-color: #4CAF50;
            --tag-bg: #e3f2fd;
            --tag-text: #1976D2;
            --logic-bg: #fff3e0;
            --logic-text: #F57C00;
            --badge-llm-bg: #2196F3;
            --badge-8step-bg: #9C27B0;
            --table-header-bg: #f5f5f5;
            --hover-shadow: rgba(0,0,0,0.15);
            --selected-border: #4CAF50;
            --selected-bg: #f8fff8;
            --btn-bg: #4CAF50;
            --btn-text: #ffffff;
        }
        [data-theme="dark"] {
            --bg: #0d1117;
            --card-bg: #161b22;
            --text: #c9d1d9;
            --text-secondary: #8b949e;
            --border: #30363d;
            --metric-bg: #21262d;
            --entry-bg: #1c2128;
            --header-border: #238636;
            --score-color: #3fb950;
            --link-color: #58a6ff;
            --tag-bg: #1f6feb33;
            --tag-text: #58a6ff;
            --logic-bg: #d2992233;
            --logic-text: #e3b341;
            --badge-llm-bg: #58a6ff;
            --badge-8step-bg: #a371f7;
            --table-header-bg: #21262d;
            --hover-shadow: rgba(0,0,0,0.5);
            --selected-border: #238636;
            --selected-bg: #0d2818;
            --btn-bg: #238636;
            --btn-text: #ffffff;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding: 16px; transition: background 0.3s, color 0.3s; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header-row { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 12px; margin-bottom: 10px; }
        h1 { color: var(--text); margin-bottom: 0; font-size: 1.5rem; }
        .subtitle { color: var(--text-secondary); margin-bottom: 20px; font-size: 0.9rem; }
        .theme-toggle { background: var(--btn-bg); color: var(--btn-text); border: none; padding: 8px 16px; border-radius: 20px; cursor: pointer; font-size: 14px; display: flex; align-items: center; gap: 6px; transition: opacity 0.2s; }
        .theme-toggle:hover { opacity: 0.85; }
        .card { background: var(--card-bg); border-radius: 12px; padding: 20px; margin-bottom: 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); border: 1px solid var(--border); transition: background 0.3s, border-color 0.3s; }
        .card h2 { color: var(--text); margin-bottom: 14px; border-bottom: 2px solid var(--header-border); padding-bottom: 8px; font-size: 1.1rem; }
        .stock-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
        .stock-card { border: 1px solid var(--border); border-radius: 8px; padding: 14px; transition: transform 0.2s, box-shadow 0.2s; background: var(--card-bg); }
        .stock-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px var(--hover-shadow); }
        .stock-card.selected { border-color: var(--selected-border); background: var(--selected-bg); }
        .stock-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 6px; }
        .stock-code { font-size: 16px; font-weight: bold; color: var(--text); }
        .stock-name { color: var(--text-secondary); margin-left: 6px; font-size: 0.9rem; }
        .score { font-size: 22px; font-weight: bold; color: var(--score-color); }
        .score-label { font-size: 11px; color: var(--text-secondary); }
        .metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin: 10px 0; }
        .metric { text-align: center; padding: 6px; background: var(--metric-bg); border-radius: 4px; }
        .metric-value { font-size: 15px; font-weight: bold; color: var(--text); }
        .metric-label { font-size: 10px; color: var(--text-secondary); }
        .entry-info { margin: 10px 0; padding: 10px; background: var(--entry-bg); border-radius: 4px; }
        .entry-row { display: flex; justify-content: space-between; margin: 3px 0; font-size: 0.9rem; }
        .entry-label { color: var(--text-secondary); }
        .entry-value { font-weight: bold; color: var(--text); }
        .sources { margin-top: 10px; }
        .source-tag { display: inline-block; padding: 3px 7px; background: var(--tag-bg); color: var(--tag-text); border-radius: 4px; margin: 2px; font-size: 11px; }
        .logic-tag { display: inline-block; padding: 3px 7px; background: var(--logic-bg); color: var(--logic-text); border-radius: 4px; margin: 2px; font-size: 11px; }
        .badge { display: inline-block; padding: 2px 7px; border-radius: 12px; font-size: 11px; font-weight: bold; }
        .badge-buy { background: #4CAF50; color: white; }
        .badge-watch { background: #FF9800; color: white; }
        .badge-strong { background: #2196F3; color: white; }
        .badge-llm { background: var(--badge-llm-bg); color: white; font-size: 10px; padding: 2px 6px; border-radius: 10px; margin-left: 4px; }
        .badge-8step { background: var(--badge-8step-bg); color: white; font-size: 10px; padding: 2px 6px; border-radius: 10px; margin-left: 4px; }
        table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid var(--border); color: var(--text); }
        th { background: var(--table-header-bg); font-weight: 600; }
        .footer { text-align: center; color: var(--text-secondary); margin-top: 30px; padding: 16px; font-size: 0.85rem; }
        .history-link { display: inline-block; margin: 6px 3px; padding: 6px 12px; background: var(--btn-bg); color: var(--btn-text); text-decoration: none; border-radius: 4px; font-size: 0.85rem; }
        .history-link:hover { opacity: 0.85; }
        .history-nav { background: var(--card-bg); border-radius: 12px; padding: 14px 18px; margin-bottom: 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); display: flex; align-items: center; gap: 10px; flex-wrap: wrap; border: 1px solid var(--border); }
        .history-nav label { font-weight: 600; color: var(--text); white-space: nowrap; font-size: 0.9rem; }
        .history-nav select { padding: 7px 14px; border: 2px solid var(--header-border); border-radius: 6px; font-size: 14px; color: var(--text); background: var(--card-bg); cursor: pointer; outline: none; }
        .history-nav select:hover { border-color: var(--score-color); }
        .history-nav select:focus { border-color: var(--badge-llm-bg); box-shadow: 0 0 0 3px rgba(33,150,243,0.2); }

        /* Responsive */
        @media (max-width: 768px) {
            body { padding: 10px; }
            h1 { font-size: 1.2rem; }
            .stock-grid { grid-template-columns: 1fr; }
            .stock-header { flex-direction: column; align-items: flex-start; }
            .metrics { grid-template-columns: repeat(3, 1fr); }
            .history-nav { flex-direction: column; align-items: stretch; }
            .history-nav select { width: 100%; }
            table { font-size: 0.8rem; }
            th, td { padding: 8px 6px; }
            .card { padding: 14px; }
            .header-row { flex-direction: column; align-items: flex-start; }
        }
        @media (max-width: 480px) {
            .stock-grid { grid-template-columns: 1fr; }
            .metric-value { font-size: 13px; }
            .score { font-size: 18px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header-row">
            <div>
                <h1>🤖 AI 股票推荐系统</h1>
                <p class="subtitle">
                    {% if run_mode == 'morning' %}
                    🌅 盘前参考 | 报告日期: {{ date }} | 生成时间: {{ generated_at }}
                    {% else %}
                    🌙 盘后复盘 | 报告日期: {{ date }} | 生成时间: {{ generated_at }}
                    {% endif %}
                </p>
            </div>
            <button class="theme-toggle" onclick="toggleTheme()" id="themeBtn">
                <span id="themeIcon">🌙</span> <span id="themeText">深色</span>
            </button>
        </div>

        <div class="history-nav">
            <label>📅 历史报告:</label>
            <select onchange="if(this.value) window.location.href=this.value;">
                <option value="">选择日期跳转</option>
                {% for d in history_dates %}
                <option value="{{ d }}/index.html" {% if d == date %}selected{% endif %}>{{ d }}</option>
                {% endfor %}
            </select>
        </div>
        
        {% if candidates %}
        <div class="card">
            <h2>
                 LLM 多源策略候选 ({{ candidates|length }} 只)
                {% if llm_date %}<span style="font-size:13px;color:#888;margin-left:8px;">{{ llm_date }}</span>{% endif %}
            </h2>
            <div class="stock-grid">
                {% for c in candidates %}
                <div class="stock-card {% if c.selected %}selected{% endif %}">
                    <div class="stock-header">
                        <div>
                            <span class="stock-code">{{ c.ts_code }}</span>
                            <span class="stock-name">{{ c.stock_name }}</span>
                            <span class="badge-llm">🤖 LLM</span>
                            {% if c.selected %}<span class="badge badge-strong">✓ 选中</span>{% endif %}
                        </div>
                        <div style="text-align: right;">
                            <div class="score">{{ "%.1f"|format(c.final_score) }}</div>
                            <div class="score-label">综合分</div>
                        </div>
                    </div>
                    
                    <div class="metrics">
                        <div class="metric">
                            <div class="metric-value">{{ "%.0f"|format(c.llm_score) }}</div>
                            <div class="metric-label">LLM 分</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">{{ "%.0f"|format(c.quant_score) }}</div>
                            <div class="metric-label">量化分</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">{{ "%.2f"|format(c.consensus_score) }}</div>
                            <div class="metric-label">共识度</div>
                        </div>
                    </div>
                    
                    <div class="entry-info">
                        <div class="entry-row">
                            <span class="entry-label">入场区间</span>
                            <span class="entry-value">{{ c.entry_low or '—' }} - {{ c.entry_high or '—' }}</span>
                        </div>
                        <div class="entry-row">
                            <span class="entry-label">止损</span>
                            <span class="entry-value" style="color: #f44336;">{{ c.stop_loss or '—' }}</span>
                        </div>
                        <div class="entry-row">
                            <span class="entry-label">目标</span>
                            <span class="entry-value" style="color: #4CAF50;">{{ c.target_1 or '—' }} / {{ c.target_2 or '—' }}</span>
                        </div>
                        {% if c.position_pct %}
                        <div class="entry-row">
                            <span class="entry-label">建议仓位</span>
                            <span class="entry-value">{{ "%.0f"|format(c.position_pct * 100) }}%</span>
                        </div>
                        {% endif %}
                    </div>
                    
                    <div class="sources">
                        {% for tag in c.logic_tags %}
                        <span class="logic-tag">{{ tag }}</span>
                        {% endfor %}
                        <div style="margin-top: 8px; font-size: 12px; color: #666;">
                            来源: {{ c.source_count }} 个 | 提及: {{ c.mention_count }} 次
                        </div>
                        {% for s in c.sources_detail %}
                        <span class="source-tag">{{ s.source }} ({{ s.tier }})</span>
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endif %}
        
        {% if step_date %}
        <div class="card">
            <h2>🔮 八步法候选{% if eight_step_picks %} ({{ eight_step_picks|length }} 只){% endif %}<span style="font-size:13px;color:#888;margin-left:8px;">{{ step_date }}</span></h2>
            {% if eight_step_picks %}
            <div class="stock-grid">
                {% for c in eight_step_picks %}
                <div class="stock-card selected">
                    <div class="stock-header">
                        <div>
                            <span class="stock-code">{{ c.ts_code }}</span>
                            <span class="stock-name">{{ c.stock_name }}</span>
                            <span class="badge-8step">🔮 八步法</span>
                            <span class="badge badge-strong">✓ 选中</span>
                        </div>
                        <div style="text-align: right;">
                            <div class="score">{{ "%.1f"|format(c.final_score) }}</div>
                            <div class="score-label">综合分</div>
                        </div>
                    </div>
                    
                    <div class="metrics">
                        <div class="metric">
                            <div class="metric-value">{{ "%.0f"|format(c.llm_score) }}</div>
                            <div class="metric-label">LLM 加成</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">{{ "%.0f"|format(c.quant_score) }}</div>
                            <div class="metric-label">量化分</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">{{ "%.2f"|format(c.consensus_score) }}</div>
                            <div class="metric-label">共识度</div>
                        </div>
                    </div>
                    
                    <div class="entry-info">
                        <div class="entry-row">
                            <span class="entry-label">入场区间</span>
                            <span class="entry-value">{{ c.entry_low or '—' }} - {{ c.entry_high or '—' }}</span>
                        </div>
                        <div class="entry-row">
                            <span class="entry-label">止损</span>
                            <span class="entry-value" style="color: #f44336;">{{ c.stop_loss or '—' }}</span>
                        </div>
                        <div class="entry-row">
                            <span class="entry-label">目标</span>
                            <span class="entry-value" style="color: #4CAF50;">{{ c.target_1 or '—' }} / {{ c.target_2 or '—' }}</span>
                        </div>
                        {% if c.position_pct %}
                        <div class="entry-row">
                            <span class="entry-label">建议仓位</span>
                            <span class="entry-value">{{ "%.0f"|format(c.position_pct * 100) }}%</span>
                        </div>
                        {% endif %}
                    </div>
                    
                    <div class="sources">
                        {% for tag in c.logic_tags %}
                        <span class="logic-tag">{{ tag }}</span>
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <p style="color: #888; text-align: center; padding: 20px;">今日八步法扫描无候选标的</p>
            {% endif %}
        </div>
        {% endif %}
        
        <div class="card">
            <h2>📡 数据源统计</h2>
            <table>
                <tr><th>数据源</th><th>信号数</th><th>平均置信度</th><th>平均强度</th></tr>
                {% for s in source_stats %}
                <tr>
                    <td>{{ s.name }}</td>
                    <td>{{ s.signal_count }}</td>
                    <td>{{ "%.2f"|format(s.avg_confidence or 0) }}</td>
                    <td>{{ "%.1f"|format(s.avg_strength or 0) }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        
        <div class="card">
            <h2>📰 最新资讯</h2>
            <table>
                <tr><th>标题</th><th>来源</th><th>时间</th><th>链接</th></tr>
                {% for a in articles %}
                <tr>
                    <td>{{ a.title or '无标题' }}</td>
                    <td>{{ a.source_name }}</td>
                    <td>{{ a.pub_time }}</td>
                    <td>{% if a.url %}<a href="{{ a.url }}" target="_blank" style="color: #4CAF50;">查看原文</a>{% else %}—{% endif %}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        
        <div class="footer">
            <p>AI Stock Recommendation System | LLM多源 + 八步法 | 每日 15:30 自动更新</p>
            <p>历史报告:
                {% for d in history_dates %}
                <a href="{{ d }}/index.html" class="history-link">{{ d }}</a>
                {% endfor %}
            </p>
        </div>
    </div>
    <script>
        (function() {
            const saved = localStorage.getItem('theme');
            const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            const isDark = saved === 'dark' || (!saved && prefersDark);
            if (isDark) {
                document.documentElement.setAttribute('data-theme', 'dark');
                document.getElementById('themeIcon').textContent = '☀️';
                document.getElementById('themeText').textContent = '浅色';
            }
        })();
        function toggleTheme() {
            const html = document.documentElement;
            const icon = document.getElementById('themeIcon');
            const text = document.getElementById('themeText');
            if (html.getAttribute('data-theme') === 'dark') {
                html.removeAttribute('data-theme');
                localStorage.setItem('theme', 'light');
                icon.textContent = '🌙';
                text.textContent = '深色';
            } else {
                html.setAttribute('data-theme', 'dark');
                localStorage.setItem('theme', 'dark');
                icon.textContent = '☀️';
                text.textContent = '浅色';
            }
        }
    </script>
</body>
</html>"""


def _get_report_snapshot_date(cur):
    cur.execute("""
        SELECT snapshot_date
        FROM daily_candidates
        WHERE source IN ('llm_multisource', 'overnight_8step')
        ORDER BY snapshot_date DESC
        LIMIT 1;
    """)
    row = cur.fetchone()
    if row:
        return row['snapshot_date']
    return get_beijing_date()


def _get_latest_source_date(cur, source, lookback_days=7):
    cur.execute("""
        SELECT MAX(snapshot_date) AS max_date
        FROM daily_candidates
        WHERE source = %s
          AND snapshot_date >= CURRENT_DATE - %s;
    """, (source, lookback_days))
    row = cur.fetchone()
    if row and row['max_date']:
        return row['max_date']
    return None


def generate_report():
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    snapshot_date = _get_report_snapshot_date(cur)
    
    llm_date = _get_latest_source_date(cur, 'llm_multisource')
    step_date = _get_latest_source_date(cur, 'overnight_8step')
    
    if llm_date:
        cur.execute("""
            SELECT * FROM daily_candidates
            WHERE snapshot_date = %s AND source = 'llm_multisource' AND run_mode = %s
            ORDER BY final_score DESC;
        """, (llm_date, RUN_MODE))
        candidates = cur.fetchall()
    else:
        candidates = []
    
    if step_date:
        cur.execute("""
            SELECT * FROM daily_candidates
            WHERE snapshot_date = %s AND source = 'overnight_8step' AND selected = TRUE
            ORDER BY final_score DESC;
        """, (step_date,))
        eight_step_picks = cur.fetchall()
    else:
        eight_step_picks = []
    
    cur.execute("""
        SELECT source_name, COUNT(*) as signal_count, source_tier
        FROM raw_signals
        WHERE fetch_time >= %s
        GROUP BY source_name, source_tier
        ORDER BY signal_count DESC;
    """, (snapshot_date - timedelta(days=2),))
    source_stats = cur.fetchall()
    
    cur.execute("""
        SELECT title, url, pub_time, source_name
        FROM raw_signals
        WHERE pub_time IS NOT NULL
        ORDER BY pub_time DESC LIMIT 20;
    """)
    articles = cur.fetchall()
    
    cur.execute("""
        SELECT DISTINCT snapshot_date FROM daily_candidates
        WHERE source IN ('llm_multisource', 'overnight_8step')
        ORDER BY snapshot_date DESC LIMIT 10;
    """)
    history_dates = [str(r['snapshot_date']) for r in cur.fetchall()]
    
    cur.close(); conn.close()
    
    display_date = str(llm_date or step_date or snapshot_date)
    llm_date_str = str(llm_date) if llm_date else None
    step_date_str = str(step_date) if step_date else None
    
    template = Template(HTML_TEMPLATE)
    html = template.render(
        date=display_date,
        generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        candidates=[dict(c) for c in candidates],
        eight_step_picks=[dict(c) for c in eight_step_picks],
        source_stats=[dict(s) for s in source_stats],
        articles=[dict(a) for a in articles],
        history_dates=history_dates,
        run_mode=RUN_MODE,
        llm_date=llm_date_str,
        step_date=step_date_str,
    )
    
    output_dir = os.path.join(BASE_DIR, 'docs', display_date)
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    
    latest_dir = os.path.join(BASE_DIR, 'docs', 'latest')
    os.makedirs(latest_dir, exist_ok=True)
    with open(os.path.join(latest_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"Report generated: {output_dir}/index.html")
    print(f"  LLM候选: {len(candidates)} 只 (snapshot_date={llm_date})")
    print(f"  八步法候选: {len(eight_step_picks)} 只 (snapshot_date={step_date})")


def generate_text_report():
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    snapshot_date = _get_report_snapshot_date(cur)
    
    llm_date = _get_latest_source_date(cur, 'llm_multisource')
    step_date = _get_latest_source_date(cur, 'overnight_8step')
    
    if llm_date:
        cur.execute("""
            SELECT * FROM daily_candidates
            WHERE snapshot_date = %s AND source = 'llm_multisource' AND run_mode = %s
            ORDER BY final_score DESC;
        """, (llm_date, RUN_MODE))
        candidates = cur.fetchall()
    else:
        candidates = []
    
    if step_date:
        cur.execute("""
            SELECT * FROM daily_candidates
            WHERE snapshot_date = %s AND source = 'overnight_8step' AND selected = TRUE
            ORDER BY final_score DESC;
        """, (step_date,))
        eight_step_picks = cur.fetchall()
    else:
        eight_step_picks = []
    
    cur.execute("""
        SELECT source_name, COUNT(*) as signal_count
        FROM raw_signals
        WHERE fetch_time >= %s
        GROUP BY source_name
        ORDER BY signal_count DESC;
    """, (snapshot_date - timedelta(days=2),))
    source_stats = cur.fetchall()
    
    cur.close(); conn.close()
    
    mode_text = "盘后复盘" if RUN_MODE == 'afternoon' else "盘前参考"
    display_date = str(llm_date or step_date or snapshot_date)
    lines = []
    lines.append(f"{display_date} {mode_text}")
    lines.append("")
    lines.append("数据采集开始：")
    
    for s in source_stats:
        lines.append(f"  {s['source_name']}: {s['signal_count']} 条")
    
    lines.append("")
    if candidates:
        lines.append(f"共 {len(candidates)} 只 LLM 候选 ({llm_date})")
    if eight_step_picks:
        lines.append(f"共 {len(eight_step_picks)} 只八步法候选 ({step_date})")
    lines.append("")
    
    if candidates:
        lines.append("🤖 LLM 多源策略:")
        for c in candidates:
            prefix = "🎯 " if c.get('selected') else "  "
            lines.append(f"{prefix}{c['stock_name']} {c['ts_code']}")
            lines.append(f"  综合分: {c['final_score']:.1f} | LLM:{c['llm_score']:.0f} 量化:{c['quant_score']:.0f}")
            if c.get('entry_low') and c.get('entry_high'):
                lines.append(f"  入场: {c['entry_low']:.3f}-{c['entry_high']:.3f}  止损: {c['stop_loss']:.3f}")
            if c.get('target_1') and c.get('target_2'):
                lines.append(f"  目标: {c['target_1']:.3f}/{c['target_2']:.3f}  仓位: {c['position_pct']*100:.0f}%")
            if c.get('logic_tags'):
                tags = c['logic_tags'] if isinstance(c['logic_tags'], list) else []
                lines.append(f"  逻辑: {', '.join(tags)}")
            lines.append("")
    
    if eight_step_picks:
        lines.append("🔮 八步法候选:")
        for c in eight_step_picks:
            lines.append(f"🎯 {c['stock_name']} {c['ts_code']}")
            lines.append(f"  综合分: {c['final_score']:.1f} | LLM加成:{c['llm_score']:.0f} 量化:{c['quant_score']:.0f}")
            if c.get('entry_low') and c.get('entry_high'):
                lines.append(f"  入场: {c['entry_low']:.3f}-{c['entry_high']:.3f}  止损: {c['stop_loss']:.3f}")
            if c.get('target_1') and c.get('target_2'):
                lines.append(f"  目标: {c['target_1']:.3f}/{c['target_2']:.3f}  仓位: {c['position_pct']*100:.0f}%")
            if c.get('logic_tags'):
                tags = c['logic_tags'] if isinstance(c['logic_tags'], list) else []
                lines.append(f"  逻辑: {', '.join(tags)}")
            lines.append("")
    
    return "\n".join(lines)


if __name__ == '__main__':
    from datetime import datetime
    generate_report()
    print("\n" + "="*50 + "\n")
    print(generate_text_report())
