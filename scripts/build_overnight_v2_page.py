"""Generate the standalone 隔夜八步法 v2.1 (14:30/14:50) report page.

Output: strategies/funnel_strategy/docs/overnight_v2.html
"""
from __future__ import annotations

import html
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "overnight_v2")
OUT = os.path.join(ROOT, "strategies", "funnel_strategy", "docs", "overnight_v2.html")


def _load(name, default=None):
    path = os.path.join(SRC, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _pct(x, digits=1):
    return f"{x * 100:+.{digits}f}%"


def _svg_equity(equity):
    if not equity:
        return "<p>暂无净值数据</p>"
    ys = [e["equity"] for e in equity]
    w, h, pad = 920, 240, 20
    lo, hi = min(ys), max(ys)
    span = (hi - lo) or 1
    coords = []
    for i, y in enumerate(ys):
        x = pad + (w - 2 * pad) * i / (len(ys) - 1)
        yy = h - pad - (h - 2 * pad) * (y - lo) / span
        coords.append(f"{x:.1f},{yy:.1f}")
    poly = " ".join(coords)
    return f"""<svg viewBox="0 0 {w} {h}" class="eq" role="img" aria-label="净值曲线">
  <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#2563eb" stop-opacity=".35"/>
    <stop offset="100%" stop-color="#2563eb" stop-opacity="0"/>
  </linearGradient></defs>
  <polyline points="{poly}" fill="none" stroke="#2563eb" stroke-width="2.2"/>
  <polygon points="{pad},{h-pad} {poly} {w-pad},{h-pad}" fill="url(#g)"/>
  <text x="{pad}" y="16" class="ax">净值 {hi:.2f}x</text>
  <text x="{pad}" y="{h-4}" class="ax">起点</text>
  <text x="{w-pad-70}" y="{h-4}" class="ax">最新 {ys[-1]:.2f}x</text>
</svg>"""


def build():
    bt = _load("intraday_backtest.json", {})
    latest = _load("latest.json", {})
    s = bt.get("stats", {})
    months = bt.get("monthly", [])

    cards = f"""
    <div class="cards">
      <div class="card"><div class="k">总收益</div><div class="v up">{_pct(s.get('total_return', 0))}</div></div>
      <div class="card"><div class="k">年化</div><div class="v up">{_pct(s.get('annualized', 0))}</div></div>
      <div class="card"><div class="k">最大回撤</div><div class="v down">{_pct(s.get('max_drawdown', 0))}</div></div>
      <div class="card"><div class="k">Sharpe</div><div class="v">{s.get('sharpe', 0):.2f}</div></div>
      <div class="card"><div class="k">单笔净收益</div><div class="v up">{_pct(s.get('avg_trade_ret', 0), 2)}</div></div>
      <div class="card"><div class="k">胜率</div><div class="v">{s.get('win_rate', 0)*100:.1f}%</div></div>
      <div class="card"><div class="k">交易 / 活跃日</div><div class="v">{s.get('trades', 0)} / {s.get('active_days', 0)}</div></div>
      <div class="card"><div class="k">成本</div><div class="v">{s.get('cost', 0)*100:.2f}%</div></div>
    </div>
    """

    month_rows = ""
    for i in range(0, len(months), 6):
        chunk = months[i:i + 6]
        month_rows += "<tr>" + "".join(
            f"<td>{m['ym']}<br><span class='{ 'up' if m['ret']>=0 else 'down'}'>{m['ret']*100:+.1f}%</span></td>"
            for m in chunk) + "</tr>"

    status = ""
    if latest and latest.get("scan_time") == "14:30":
        cands = latest.get("candidates", [])
        status = f"""
        <section class="block">
          <h2>最新扫描 · {html.escape(latest.get('trade_date',''))} 14:30</h2>
          <p class="muted">全市场 {latest.get('universe',0)} 只 · 广度 {latest.get('breadth',0)*100:.1f}% ·
            大盘门控 <b class="{'up' if latest.get('risk_on') else 'down'}">
            {'开启' if latest.get('risk_on') else '关闭'}</b>
            {'· <b class="down">只观察不买</b>' if latest.get('blocked') else ''}</p>
          <table><thead><tr><th>代码</th><th>名称</th><th>评分</th><th>14:50 参考价</th><th>止损</th><th>执行</th></tr></thead><tbody>
          {''.join(f"<tr><td>{html.escape(c['ts_code'])}</td><td>{html.escape(str(c.get('stock_name') or ''))}</td>"
                   f"<td>{c['score']}</td><td>{c['entry_low']} ~ {c['entry_high']}</td>"
                   f"<td>{c['stop_loss']}</td><td>{'买入' if c.get('selected') else '观察不买'}</td></tr>"
                   for c in cands) or '<tr><td colspan="6">本期无候选</td></tr>'}
          </tbody></table>
        </section>
        """

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>隔夜八步法 v2.1 · 尾盘恐慌承接</title>
<style>
:root {{ --bg:#f7f9fc; --card:#fff; --text:#0f172a; --muted:#64748b; --line:#e2e8f0; --up:#dc2626; --down:#16a34a; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0b1220; --card:#111827; --text:#e5e7eb; --muted:#94a3b8; --line:#1f2937; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:24px; background:var(--bg); color:var(--text);
  font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
.wrap {{ max-width:1040px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 4px; }}
h2 {{ font-size:17px; margin:0 0 12px; }}
.sub {{ color:var(--muted); margin-bottom:20px; }}
.block {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin-bottom:18px; }}
.cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }}
.card {{ border:1px solid var(--line); border-radius:10px; padding:10px 12px; }}
.card .k {{ color:var(--muted); font-size:12px; }}
.card .v {{ font-size:19px; font-weight:700; }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th,td {{ border-bottom:1px solid var(--line); padding:7px 8px; text-align:left; }}
th {{ color:var(--muted); font-weight:600; }}
.eq {{ width:100%; height:auto; }}
.ax {{ fill:var(--muted); font-size:11px; }}
.muted {{ color:var(--muted); }}
ul {{ margin:8px 0 0 18px; padding:0; }} li {{ margin:4px 0; }}
.warn {{ border-left:4px solid #f59e0b; padding:10px 14px; background:rgba(245,158,11,.08); border-radius:6px; margin-top:10px; }}
a {{ color:#2563eb; }}
@media (max-width:720px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} }}
</style></head><body><div class="wrap">
<h1>🌙 隔夜八步法 v2.1 · 尾盘恐慌承接</h1>
<div class="sub">14:30 扫描 → 14:50 买入 → 次日 09:30-10:00 卖出（优先 09:50）· 5 分钟线样本回测 · 往返成本 0.25%</div>

{cards}

<section class="block">
  <h2>为什么旧八步法在同样节奏下会亏</h2>
  <ul>
    <li>旧八步法要求“涨 3~9.7% + 换手 5~10% + 量比放大”，这是在追当天已经涨起来的票；14:50 买入后，次日开盘平均 <b class="down">−0.21%</b>，09:40 卖 <b class="down">−0.34%</b>。</li>
    <li>同一节奏下，把条件反过来：<b>强趋势里回踩 −7%~−2%、14:30 收盘位置 &lt; 0.2（恐慌盘）、换手 1.5~8%</b>，
        14:50 买入、次日 09:40 卖，单笔 <b class="up">+0.47%</b>（未扣费），扣 0.25% 后 <b class="up">+0.33%</b>。</li>
    <li>关键不是“买强势”，而是“在上升趋势里接恐慌盘”，并且用次日开盘缺口决定卖点。</li>
  </ul>
</section>

<section class="block">
  <h2>净值曲线（2025-01 ~ 2026-04，满仓等权，空仓日计 0）</h2>
  {_svg_equity(bt.get('equity', []))}
</section>

<section class="block">
  <h2>八步规则（当前实盘口径）</h2>
  <table><thead><tr><th>步</th><th>条件</th></tr></thead><tbody>
    <tr><td>1 大盘环境</td><td>等权指数 &gt; 10 日线；全市场站上 MA20 比例 20%~70%</td></tr>
    <tr><td>2 趋势结构</td><td>T-1：收盘 &gt; MA20 &gt; MA60，20 日动量 &gt; 3%；14:30 价格仍 &gt; MA20</td></tr>
    <tr><td>3 尾盘回踩</td><td>14:30 涨跌幅 −7.0% ~ −2.0%</td></tr>
    <tr><td>4 恐慌收盘</td><td>14:30 价格位于当日振幅下 20%</td></tr>
    <tr><td>5 流动性</td><td>成交额 ≥ 1 亿，流通市值 50~2000 亿</td></tr>
    <tr><td>6 换手适中</td><td>累计换手率 1.5% ~ 8%</td></tr>
    <tr><td>7 买入</td><td>14:50 左右买入，单票 ≤20%，最多 5 只</td></tr>
    <tr><td>8 卖出</td><td>次日 09:25 看缺口：浮盈 ≥+0.5% 或浮亏 ≤−3% → 开盘卖；否则 −2% 止损，09:50 前卖，最晚 10:00</td></tr>
  </tbody></table>
</section>

{status}

<section class="block">
  <h2>月度收益</h2>
  <table><tbody>{month_rows}</tbody></table>
</section>

<section class="block">
  <h2>执行纪律</h2>
  <ul>
    <li>14:30 扫描出观察池（最多 30 只，14:30 收盘位置越低越靠前）。</li>
    <li>14:50 前后买入，单票 ≤20%，最多 5 只；不追已拉回的票（14:50 价格明显高于 14:30 的不买）。</li>
    <li>次日 09:25 看开盘：浮盈 ≥+0.5% 直接卖；浮亏 ≤−3% 直接卖；其余设 −2% 止损，09:50 前卖出，最晚 10:00。</li>
    <li>大盘门控关闭（指数跌破 10 日线或广度不在 20%~70%）→ 空仓。</li>
  </ul>
  <div class="warn">⚠️ 样本为 600 个候选信号的 5 分钟线回测（91 笔交易、62 个活跃日），不是全量逐日回测；
  单笔 +0.33% 净收益、年化 +14.5%、最大回撤 −5.0%。实盘会受滑点、成交、T+1 无法当日止损影响。
  该策略只在约 20% 的交易日出手，其余时间空仓。</div>
</section>

<p class="muted" style="text-align:center;margin:24px 0">
  隔夜八步法 v2.1 · 数据源 daily_quotes + 腾讯实时快照 + baostock 5 分钟线 · 仅供研究，不构成投资建议 ·
  <a href="./funnel.html">返回六策略对比</a>
</p>
</div></body></html>
"""


def main():
    html_text = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_text)
    print("saved", OUT, len(html_text), "bytes")


if __name__ == "__main__":
    main()
