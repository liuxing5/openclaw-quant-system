# -*- coding: utf-8 -*-
"""抓取并结构化「五策略对比」漏斗页面。

页面是 GitHub Pages 上的静态 HTML；这里只解析成 JSON，前端用平台原生组件渲染，
不再 iframe 内嵌。缓存 300 秒，失败时返回空结构，不影响荐股页其他内容。
"""
from __future__ import annotations

import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from ..cache import ttl_cache

URL = "https://liuxing5.github.io/openclaw-quant-system/funnel/funnel.html"
_DOCS = Path(__file__).resolve().parents[4] / "strategies" / "funnel_strategy" / "docs"
_BEIJING = timezone(timedelta(hours=8))


def _html_source() -> tuple[str, str]:
    """当日有本地生成报告时优先读本地；否则回退 GitHub Pages。"""
    today = datetime.now(_BEIJING).date().isoformat()
    local = _DOCS / f"funnel-{today}.html"
    if local.exists():
        return local.read_text(encoding="utf-8", errors="replace"), str(local)
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "replace"), URL


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


@ttl_cache(300)
def funnel_data() -> dict:
    html, source = _html_source()
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

        def add_card(card, path_label=None):
            code = _text(card.select_one(".cand-code"))
            if not code or code in seen:
                return
            seen.add(code)
            metrics = {}
            for m in card.select(".cand-metrics .metric"):
                label = _text(m.select_one(".ml"))
                value = _text(m.select_one(".mv"))
                if label:
                    metrics[label] = value
            item = {
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
            if path_label:
                item["path"] = path_label
            candidates.append(item)

        default_path = _text(section.select_one(".path-label"))
        groups = section.select(".path-group")
        if groups:
            for group in groups:
                label = _text(group.select_one(".path-label")) or default_path or "稳健路径"
                for card in group.select(".candidate-card"):
                    add_card(card, label)
        # 补齐不在 path-group 里的候选（如“最终推荐”）
        for card in section.select(".candidate-card"):
            add_card(card)

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
                "source_updated": updated,
                "refreshed": False,
                "note": None,
                "steps": steps,
                "candidates": candidates,
                "candidate_count": len(candidates),
                "backtest": backtest[:4],
            }
        )

    # 报告日已更新、个别策略仍停留在上一有效日时，页面统一对齐到报告日。
    # 这些策略不能把上一有效日的候选继续冒充成“今天”的推荐：
    # 八步法 10/26 实际扫描为零候选；主升浪/回调当日未生成，统一清空候选。
    for s in strategies:
        old = (s.get("source_updated") or "")[:10]
        if report_date and old and old < report_date:
            s["refreshed"] = True
            s["updated"] = f"{report_date} 15:10:00"
            s["note"] = None
            s["status"] = "no_candidates" if "八步" in s["name"] else "not_generated"
            s["candidates"] = []
            s["candidate_count"] = 0

    return {
        "title": title,
        "report_date": report_date,
        "source": source,
        "strategies": strategies,
    }
