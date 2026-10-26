# -*- coding: utf-8 -*-
"""抓取并结构化「五策略对比」漏斗页面。

页面是 GitHub Pages 上的静态 HTML；这里只解析成 JSON，前端用平台原生组件渲染，
不再 iframe 内嵌。缓存 300 秒，失败时返回空结构，不影响荐股页其他内容。
"""
from __future__ import annotations

import re
import urllib.request

from bs4 import BeautifulSoup

from ..cache import ttl_cache

URL = "https://liuxing5.github.io/openclaw-quant-system/funnel/funnel.html"


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


@ttl_cache(300)
def funnel_data() -> dict:
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", "replace")
    soup = BeautifulSoup(html, "lxml")
    title = _text(soup.find("h1")) or "五策略对比选股系统"
    date_input = soup.find("input", {"type": "date"})
    report_date = date_input.get("value") if date_input else None

    strategies = []
    for idx, section in enumerate(soup.select("div.section")):
        h2 = section.find("h2")
        if not h2:
            continue
        updated = _text(h2.find(class_="section-subtitle"))
        name = _text(h2).replace(updated, "").strip() if updated else _text(h2)
        steps = []
        for row in section.select(".step-row"):
            step_name = _text(row.select_one(".step-name"))
            if step_name in ("运行时间", "扫描日期"):
                continue
            steps.append(
                {
                    "name": step_name,
                    "pass": _text(row.select_one(".step-pass")),
                    "eliminated": _text(row.select_one(".elim")),
                    "rule": _text(row.select_one(".rule-inline")),
                }
            )
        candidates = []
        seen = set()
        for card in section.select(".candidate-card"):
            code = _text(card.select_one(".cand-code"))
            if not code or code in seen:
                continue
            seen.add(code)
            metrics = {}
            for m in card.select(".cand-metrics .metric"):
                label = _text(m.select_one(".ml"))
                value = _text(m.select_one(".mv"))
                if label:
                    metrics[label] = value
            candidates.append(
                {
                    "code": code,
                    "name": _text(card.select_one(".cand-name")),
                    "score": _text(card.select_one(".cand-score")),
                    "entry": metrics.get("入场"),
                    "stop": metrics.get("止损"),
                    "target": metrics.get("目标"),
                    "quant_score": metrics.get("量化分"),
                    "tags": [x.strip() for x in _text(card.select_one(".cand-tags")).split("·") if x.strip()],
                    "source": _text(card.select_one(".src-badge")),
                }
            )

        backtest = []
        table = section.find("table")
        if table:
            headers = [_text(x) for x in table.select("thead th")]
            for tr in table.select("tbody tr"):
                cells = [_text(x) for x in tr.select("td")]
                if headers and len(cells) == len(headers):
                    backtest.append(dict(zip(headers, cells)))

        strategies.append(
            {
                "id": f"s{idx + 1}",
                "name": name,
                "updated": updated,
                "steps": steps,
                "candidates": candidates,
                "candidate_count": len(candidates),
                "backtest": backtest[:4],
            }
        )

    return {
        "title": title,
        "report_date": report_date,
        "source": URL,
        "strategies": strategies,
    }
