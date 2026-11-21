"""Generate the standalone 隔夜八步法 v2 report page.

Output: strategies/funnel_strategy/docs/overnight_v2.html
Publishes to https://liuxing5.github.io/openclaw-quant-system/funnel/overnight_v2.html
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
    pts = [(i, e["equity"]) for i, e in enumerate(equity)]
    w, h, pad = 920, 240, 20
    ys = [p[1] for p in pts]
    lo, hi = min(ys), max(ys)
    span = (hi - lo) or 1
    coords = []
    for i, y in pts:
        x = pad + (w - 2 * pad) * i / (len(pts) - 1)
        yy = h - pad - (h - 2 * pad) * (y - lo) / span
        coords.append(f"{x:.1f},{yy:.1f}")
    poly = " ".join(coords)
    # drawdown shading omitted for simplicity
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
    bt = _load("backtest.json", {})
    oos = _load("oos_2026H2.json", {})
    latest = _load("latest.json", {})

    s = bt.get("stats", {})
    years = bt.get("yearly", [])
    months = bt.get("monthly", [])

    cards = f"""
    <div class="cards">
      <div class="card"><div class="k">总收益</div><div class="v up">{_pct(s.get('total_return', 0))}</div></div>
      <div class="card"><div class="k">年化</div><div class="v up">{_pct(s.get('annualized', 0))}</div></div>
      <div class="card"><div class="k">最大回撤</div><div class="v down">{_pct(s.get('max_drawdown', 0))}</div></div>
      <div class="card"><div class="k">Sharpe</div><div class="v">{s.get('sharpe', 0):.2f}</div></div>
      <div class="card"><div class="k">单笔均值</div><div class="v up">{_pct(s.get('avg_trade_ret', 0), 2)}</div></div>
      <div class="card"><div class="k">胜率</div><div class="v">{s.get('win_rate', 0)*100:.1f}%</div></div>
      <div class="card"><div class="k">交易 / 活跃日</div><div class="v">{s.get('trades', 0)} / {s.get('trading_days', 0)}</div></div>
      <div class="card"><div class="k">成本</div><div class="v">{s.get('cost', 0)*100:.2f}%</div></div>
    </div>
    """

    year_rows = "".join(
        f"<tr><td>{y['year']}</td><td class='{ 'up' if y['return']>=0 else 'down'}'>"
        f"{_pct(y['return'])}</td><td>{y['win_day']*100:.1f}%</td>"
        f"<td class='{'up' if y.get('bench',0)>=0 else 'down'}'>{_pct(y.get('bench',0))}</td></tr>"
        for y in years
    )

    month_rows = ""
    for i in range(0, len(months), 6):
        chunk = months[i:i+6]
        month_rows += "<tr>" + "".join(
            f"<td>{m['ym']}<br><span class='{ 'up' if m['ret']>=0 else 'down'}'>{m['ret']*100:+.1f}%</span></td>"
            for m in chunk) + "</tr>"

    status = ""
    if latest:
        cands = latest.get("candidates", [])
        status = f"""
        <section class="block">
          <h2>最新扫描 · {html.escape(latest.get('trade_date',''))}</h2>
          <p class="muted">全市场 {latest.get('universe',0)} 只 · 广度 {latest.get('breadth',0)*100:.1f}% ·
            大盘门控 <b class="{'up' if latest.get('risk_on') else 'down'}">
            {'开启' if latest.get('risk_on') else '关闭'}</b>
            {'· <b class="down">空仓观察</b>' if latest.get('blocked') else ''}</p>
          <table><thead><tr><th>代码</th><th>名称</th><th>分值</th><th>承接区间</th><th>执行</th></tr></thead><tbody>
          {''.join(f"<tr><td>{html.escape(c['ts_code'])}</td><td>{html.escape(str(c.get('stock_name') or ''))}</td>"
                   f"<td>{c['score']}</td><td>{c['entry_low']} ~ {c['entry_high']}</td>"
                   f"<td>{'买入' if c.get('selected') else '观察不买'}</td></tr>" for c in cands) or '<tr><td colspan="5">本期无候选</td></tr>'}
          </tbody></table>
        </section>
        """

    oos_html = ""
    if oos:
        st = oos.get("stats", {})
        oos_html = f"""
        <section class="block">
          <h2>样本外（2026-05 之后）</h2>
          <p class="muted">区间 {oos.get('period',['',''])[0]} ~ {oos.get('period',['',''])[1]}；
            门控关闭期自动空仓。</p>
          <div class="cards">
            <div class="card"><div class="k">交易</div><div class="v">{st.get('trades',0)}</div></div>
            <div class="card"><div class="k">总收益</div><div class="v">{_pct(st.get('total',0))}</div></div>
            <div class="card"><div class="k">最大回撤</div><div class="v down">{_pct(st.get('maxdd',0))}</div></div>
          </div>
        </section>
        """

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>隔夜八步法 v2 · 恐慌缺口承接</title>
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
.block {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:18px 20px; margin-bottom:18px; }}
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
.warn {{ border-left:4px solid #f59e0b; padding:10px 14px; background:rgba(245,158,11,.08);
  border-radius:6px; margin-top:10px; }}
a {{ color:#2563eb; }}
@media (max-width:720px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} }}
</style></head><body><div class="wrap">
<h1>🌙 隔夜八步法 v2 · 恐慌缺口承接</h1>
<div class="sub">T 日收盘观察 → T+1 开盘按缺口承接 → T+2 收盘离场 · 前复权 · 往返成本 0.25%</div>

{cards}

<section class="block">
  <h2>为什么改：旧版在跟「隔夜负漂移」对着干</h2>
  <ul>
    <li>全 A 股隔夜（T 收→T+1 开）均值 <b>−0.01%</b>（t=−4.0），次日日内 <b>+0.19%</b>（t=+73）——隔夜是负的。</li>
    <li>旧八步法要求涨幅 3~9.7%、换手 5~10%、量比 1.5~8，恰好落在隔夜收益最差的桶里（最高换手档 −0.46%）。</li>
    <li>同一批「强趋势回踩」标的：T 日收盘买、T+1 收盘卖 <b class="down">−36.0%</b>；
        T+1 开盘买、T+2 收盘卖 <b class="up">+375.4%</b>。差别只是等了一个隔夜缺口。</li>
  </ul>
</section>

<section class="block">
  <h2>净值曲线（2024-10 ~ 2026-04）</h2>
  {_svg_equity(bt.get('equity', []))}
</section>

<section class="block">
  <h2>八步规则</h2>
  <table><thead><tr><th>步</th><th>条件</th></tr></thead><tbody>
    <tr><td>1 大盘环境</td><td>等权指数 &gt; 10 日线，且全市场站上 MA20 比例 ≥ 30%</td></tr>
    <tr><td>2 趋势结构</td><td>收盘 &gt; MA20 &gt; MA60，20 日动量 &gt; 3%</td></tr>
    <tr><td>3 回踩幅度</td><td>当日 −6.0% ~ −1.5%</td></tr>
    <tr><td>4 恐慌收盘</td><td>收盘价位于当日振幅下 40%</td></tr>
    <tr><td>5 流动性</td><td>成交额 ≥ 1 亿，流通市值 50~2000 亿</td></tr>
    <tr><td>6 换手适中</td><td>换手率 1.5% ~ 8%</td></tr>
    <tr><td>7 缺口触发</td><td>T+1 开盘缺口 −4.0% ~ −1.0% 才买</td></tr>
    <tr><td>8 执行风控</td><td>最多 5 只等权；T+1 开盘买、T+2 收盘卖；ST/次新/涨跌停开盘剔除</td></tr>
  </tbody></table>
</section>

{status}

<section class="block">
  <h2>分段表现</h2>
  <table><thead><tr><th>年份</th><th>策略</th><th>日胜率</th><th>等权基准</th></tr></thead>
  <tbody>{year_rows}</tbody></table>
</section>

<section class="block">
  <h2>月度收益</h2>
  <table><tbody>{month_rows}</tbody></table>
</section>

{oos_html}

<section class="block">
  <h2>执行纪律</h2>
  <ul>
    <li>T 日 15:10 后扫描，得到观察池（最多 12 只，收盘越弱越靠前）。</li>
    <li>T+1 09:25 看集合竞价：只有缺口 −4.0%~−1.0% 才买；高开、平开、缺口 &lt; −4% 全部放弃。</li>
    <li>最多 5 只等权，单票 ≤ 20% 总仓位。</li>
    <li>T+2 收盘卖出（T+1 当天不能卖）；不设盘中止损，靠仓位与分散控制回撤。</li>
    <li>大盘门控关闭（广度 &lt;30% 或指数在 10 日线下）→ 空仓，不降标准硬选。</li>
  </ul>
  <div class="warn">⚠️ 回测不等于实盘：开盘价成交、滑点、涨跌停封单、T+1 无法止损都会侵蚀收益。
  本策略在熊市会长期空仓，年化会显著低于回测；单票建议 10~15%。</div>
</section>

<p class="muted" style="text-align:center;margin:24px 0">
  隔夜八步法 v2 · 数据源 daily_quotes · 仅供研究，不构成投资建议 ·
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
