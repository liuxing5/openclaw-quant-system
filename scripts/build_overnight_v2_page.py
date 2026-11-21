"""Generate the standalone 隔夜八步法 v3.0 (融合版) report page."""
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


def _pct(x, d=1):
    return f"{x * 100:+.{d}f}%"


def _cls(x):
    return "up" if x >= 0 else "down"


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
    bt = _load("v3_backtest.json", {})
    latest = _load("latest.json", {})
    s = bt.get("stats", {})
    months = bt.get("monthly", [])
    trades = bt.get("trades", [])

    cards = f"""
    <div class="cards">
      <div class="card"><div class="k">胜率</div><div class="v">{s.get('win_rate',0)*100:.1f}%</div></div>
      <div class="card"><div class="k">单笔净收益</div><div class="v up">{_pct(s.get('avg_trade_net',0),2)}</div></div>
      <div class="card"><div class="k">年化</div><div class="v up">{_pct(s.get('annualized',0))}</div></div>
      <div class="card"><div class="k">最大回撤</div><div class="v down">{_pct(s.get('max_drawdown',0))}</div></div>
      <div class="card"><div class="k">总收益</div><div class="v up">{_pct(s.get('total_return',0))}</div></div>
      <div class="card"><div class="k">Sharpe / Calmar</div><div class="v">{s.get('sharpe',0):.2f} / {s.get('calmar',0):.1f}</div></div>
      <div class="card"><div class="k">交易 / 活跃日</div><div class="v">{s.get('trades',0)} / {s.get('active_days',0)}</div></div>
      <div class="card"><div class="k">成本 / t值</div><div class="v">{s.get('cost',0)*100:.2f}% / {s.get('t_stat',0):.1f}</div></div>
    </div>
    """

    year_rows = "".join(
        f"<tr><td>{y}</td><td>{v['n_days']}</td><td class='{_cls(v['mean'])}'>{_pct(v['mean'],2)}</td>"
        f"<td>{v['win_day']*100:.1f}%</td><td class='{_cls(v['total'])}'>{_pct(v['total'])}</td>"
        f"<td class='down'>{_pct(v['dd'])}</td></tr>"
        for y, v in s.get("yearly", {}).items())
    q_rows = "".join(
        f"<tr><td>{q}</td><td>{v['n_days']}</td><td class='{_cls(v['mean'])}'>{_pct(v['mean'],2)}</td>"
        f"<td>{v['win_day']*100:.1f}%</td><td class='{_cls(v['total'])}'>{_pct(v['total'])}</td></tr>"
        for q, v in s.get("quarterly", {}).items())
    exit_rows = "".join(
        f"<tr><td>{k}</td><td>{v['n']}</td><td class='{_cls(v['mean'])}'>{_pct(v['mean'],2)}</td>"
        f"<td>{v['win']*100:.1f}%</td><td class='{_cls(v['total'])}'>{_pct(v['total'])}</td></tr>"
        for k, v in s.get("by_exit", {}).items())
    var_rows = "".join(
        f"<tr><td>{k}</td><td>{v['trades']}</td><td class='{_cls(v['avg_net'])}'>{_pct(v['avg_net'],2)}</td>"
        f"<td>{v['win']*100:.1f}%</td><td class='{_cls(v['ann'])}'>{_pct(v['ann'])}</td>"
        f"<td class='down'>{_pct(v['dd'])}</td></tr>"
        for k, v in s.get("variants", {}).items())
    loo_rows = "".join(
        f"<tr><td>{k.replace('drop_','去掉 ')}</td><td>{v['trades']}</td>"
        f"<td class='{_cls(v['avg_net'])}'>{_pct(v['avg_net'],2)}</td><td>{v['win']*100:.1f}%</td>"
        f"<td class='{_cls(v['ann'])}'>{_pct(v['ann'])}</td><td class='down'>{_pct(v['dd'])}</td></tr>"
        for k, v in s.get("loo", {}).items())
    cost_rows = "".join(
        f"<tr><td>{float(k)*100:.2f}%</td><td class='{_cls(v['avg_net'])}'>{_pct(v['avg_net'],2)}</td>"
        f"<td>{v['win']*100:.1f}%</td><td class='{_cls(v['ann'])}'>{_pct(v['ann'])}</td>"
        f"<td class='down'>{_pct(v['dd'])}</td></tr>"
        for k, v in s.get("cost_sensitivity", {}).items())
    month_rows = ""
    for i in range(0, len(months), 6):
        chunk = months[i:i + 6]
        month_rows += "<tr>" + "".join(
            f"<td>{m['ym']}<br><span class='{_cls(m['ret'])}'>{m['ret']*100:+.1f}%</span></td>"
            for m in chunk) + "</tr>"
    trade_rows = "".join(
        f"<tr><td>{t['date']}</td><td>{t['code']}</td><td>{t['pct1430']:+.2f}%</td>"
        f"<td>{t['pos1430']:.2f}</td><td>{t['vwap_pos']*100:+.1f}%</td><td>{t['lu20']}</td>"
        f"<td>{t['ret5']*100:+.1f}%</td><td>{t['amt_ratio']:.2f}</td><td>{t['exit']}</td>"
        f"<td class='{_cls(t['net'])}'>{t['net']*100:+.2f}%</td></tr>"
        for t in trades)

    boot = s.get("bootstrap", {})
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
<title>隔夜八步法 v3.0 · 融合版</title>
<style>
:root {{ --bg:#f7f9fc; --card:#fff; --text:#0f172a; --muted:#64748b; --line:#e2e8f0; --up:#dc2626; --down:#16a34a; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0b1220; --card:#111827; --text:#e5e7eb; --muted:#94a3b8; --line:#1f2937; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:24px; background:var(--bg); color:var(--text);
  font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
.wrap {{ max-width:1100px; margin:0 auto; }}
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
.note {{ border-left:4px solid #2563eb; padding:10px 14px; background:rgba(37,99,235,.06); border-radius:6px; margin-top:10px; }}
a {{ color:#2563eb; }}
.scroll {{ max-height:520px; overflow:auto; }}
@media (max-width:720px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} }}
</style></head><body><div class="wrap">
<h1>🌙 隔夜八步法 v3.0 · 融合版</h1>
<div class="sub">14:30 扫描 → 14:40-15:00 买入 → 次日 10:00 前卖出 · 融合永兴 VWAP/涨停基因、七步漏斗风控、主升浪量能、回调买入止损 · 0.25% 往返成本</div>

{cards}

<section class="block">
  <h2>结论：把「追涨」换成「接恐慌」，胜率和回撤同时改善</h2>
  <ul>
    <li>旧八步法在同一节奏下：14:50 买、次日 09:40 卖，<b class="down">−0.34%/笔</b>、胜率 39.5%。</li>
    <li>永兴原版（涨 3~5%、回踩 VWAP、强于大盘）：同期 <b class="down">−0.52%/笔</b>、胜率 41.0%——方向不对，VWAP 也救不了。</li>
    <li>v3.0：强趋势里接「恐慌回踩 + VWAP 不破 + 无近期涨停 + 5 日趋势未坏 + 量能正常」，
        <b class="up">+1.12%/笔</b>、胜率 <b>79.1%</b>、年化 <b>+29.4%</b>、最大回撤 <b>−2.25%</b>。</li>
  </ul>
  <div class="note">永兴八步法真正有用的不是「追涨 3~5%」，而是 <b>VWAP 不破</b> 和 <b>不追近期涨停</b>；
  前者用来确认回踩有承接，后者用来避开拥挤。这两条正是 v3 相对 v2.1 提升最大的过滤（去掉 VWAP 后胜率从 79% 降到 73%）。</div>
</section>

<section class="block">
  <h2>净值曲线（2025-01 ~ 2026-04，满仓等权，空仓日计 0）</h2>
  {_svg_equity(bt.get('equity', []))}
</section>

<section class="block">
  <h2>八步规则</h2>
  <table><thead><tr><th>步</th><th>条件</th><th>来源/作用</th></tr></thead><tbody>
    <tr><td>1 大盘环境</td><td>等权指数 &gt; 10 日线；全市场站上 MA20 比例 20%~70%</td><td>七步漏斗 L0 / Faber 趋势过滤</td></tr>
    <tr><td>2 趋势结构</td><td>T-1：收盘 &gt; MA20 &gt; MA60、20 日动量 &gt; 3%、5 日趋势 &gt; -2%、近 20 日涨停 ≤1 次</td><td>主升浪趋势 + 永兴「不追涨停」</td></tr>
    <tr><td>3 尾盘回踩</td><td>14:30 涨跌幅 −7.0% ~ −2.0%</td><td>O'Neil / 回调买入</td></tr>
    <tr><td>4 恐慌收盘</td><td>14:30 价格在当日振幅下 20%，且不低于 VWAP 的 −2%</td><td>恐慌出清 + 永兴「回踩 VWAP 不破」</td></tr>
    <tr><td>5 流动性</td><td>成交额 ≥ 1 亿，流通市值 50~2000 亿</td><td>七步漏斗 L2</td></tr>
    <tr><td>6 换手量能</td><td>累计换手 1.5%~8%，量能 ≥ 20 日均额的 80%</td><td>主升浪量能 + 回调缩量</td></tr>
    <tr><td>7 买入</td><td>14:40-15:00 买入，单票 ≤20%，最多 5 只</td><td>尾盘承接</td></tr>
    <tr><td>8 离场</td><td>次日 09:25 看缺口：浮盈 ≥+0.5% 或浮亏 ≤−3% 开盘卖；否则 −2% 止损，10:00 前卖</td><td>缺口决策 + 波动率止损</td></tr>
  </tbody></table>
</section>

{status}

<section class="block">
  <h2>年度 / 季度</h2>
  <table><thead><tr><th>年度</th><th>活跃日</th><th>日均</th><th>日胜率</th><th>年度收益</th><th>回撤</th></tr></thead>
  <tbody>{year_rows}</tbody></table>
  <h2 style="margin-top:18px">季度</h2>
  <table><thead><tr><th>季度</th><th>活跃日</th><th>日均</th><th>日胜率</th><th>季度收益</th></tr></thead>
  <tbody>{q_rows}</tbody></table>
</section>

<section class="block">
  <h2>卖出类型拆解</h2>
  <table><thead><tr><th>类型</th><th>笔数</th><th>均值</th><th>胜率</th><th>累计</th></tr></thead>
  <tbody>{exit_rows}</tbody></table>
  <p class="muted" style="margin-top:8px">high_open：09:25 浮盈≥+0.5% 开盘卖；stop：盘中触 −2%；time：持到 10:00 前卖；
  浮亏≤−3% 的 panic_open 本期未触发。16 笔高开全部盈利，说明「浮盈先落袋」是胜率的主要来源。</p>
</section>

<section class="block">
  <h2>因子贡献（单过滤 vs 全过滤 / 留一法）</h2>
  <table><thead><tr><th>变体</th><th>笔数</th><th>单笔净</th><th>胜率</th><th>年化</th><th>回撤</th></tr></thead>
  <tbody>{var_rows}</tbody></table>
  <h2 style="margin-top:18px">留一法（去掉一个过滤）</h2>
  <table><thead><tr><th>去掉</th><th>笔数</th><th>单笔净</th><th>胜率</th><th>年化</th><th>回撤</th></tr></thead>
  <tbody>{loo_rows}</tbody></table>
  <p class="muted" style="margin-top:8px">四个质量过滤单独用都能提升；全开后笔数从 91 降到 43，单笔从 +0.33% 提升到 +1.12%。
  VWAP 过滤贡献最大（去掉后回撤从 −2.25% 扩大到 −4.67%）。</p>
</section>

<section class="block">
  <h2>稳健性</h2>
  <table><thead><tr><th>项目</th><th>结果</th></tr></thead><tbody>
    <tr><td>Bootstrap 95% 单笔均值</td><td>{boot.get('mean_ci95',[0,0])[0]*100:+.2f}% ~ {boot.get('mean_ci95',[0,0])[1]*100:+.2f}%</td></tr>
    <tr><td>Bootstrap 95% 胜率</td><td>{boot.get('win_ci95',[0,0])[0]*100:.1f}% ~ {boot.get('win_ci95',[0,0])[1]*100:.1f}%</td></tr>
    <tr><td>最大连胜 / 连亏</td><td>{s.get('max_win_streak',0)} / {s.get('max_loss_streak',0)}</td></tr>
    <tr><td>每票 20% 仓位（其余现金）</td><td>年化 {_pct(s.get('slot20',{}).get('ann',0))} · 回撤 {_pct(s.get('slot20',{}).get('dd',0))}</td></tr>
  </tbody></table>
  <h2 style="margin-top:18px">成本敏感性</h2>
  <table><thead><tr><th>往返成本</th><th>单笔净</th><th>胜率</th><th>年化</th><th>回撤</th></tr></thead>
  <tbody>{cost_rows}</tbody></table>
</section>

<section class="block">
  <h2>月度收益</h2>
  <table><tbody>{month_rows}</tbody></table>
</section>

<section class="block">
  <h2>逐笔明细（{len(trades)} 笔）</h2>
  <div class="scroll"><table><thead><tr><th>日期</th><th>代码</th><th>14:30涨幅</th><th>位置</th><th>VWAP</th>
  <th>涨停基因</th><th>5日趋势</th><th>量能</th><th>退出</th><th>净收益</th></tr></thead>
  <tbody>{trade_rows}</tbody></table></div>
</section>

<section class="block">
  <h2>执行纪律</h2>
  <ul>
    <li>14:30 扫描出观察池（最多 30 只，14:30 收盘位置越低越靠前）。</li>
    <li>14:40-15:00 买入，单票 ≤20%，最多 5 只；14:50 后价格明显高于 14:30 的不追。</li>
    <li>次日 09:25 看开盘：浮盈 ≥+0.5% 直接卖；浮亏 ≤−3% 直接卖；其余设 −2% 止损，10:00 前卖出。</li>
    <li>大盘门控关闭（指数跌破 10 日线或广度不在 20%~70%）→ 空仓。</li>
  </ul>
  <div class="warn">⚠️ 样本为 600 个候选信号的 5 分钟线回测（43 笔、34 个活跃日），不是全量逐日回测；
  单笔 +1.12%、年化 +29.4%、最大回撤 −2.25%。Bootstrap 95% 单笔均值 +0.65%~+1.65%，
  但样本量小、2026 年只有 7 个活跃日；实盘必须按 20% 仓位、先用小资金验证 3~6 个月。</div>
</section>

<p class="muted" style="text-align:center;margin:24px 0">
  隔夜八步法 v3.0 · 数据源 daily_quotes + 腾讯实时快照 + baostock 5 分钟线 · 仅供研究，不构成投资建议 ·
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
