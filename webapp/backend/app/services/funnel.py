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

BASE_URL = "https://liuxing5.github.io/openclaw-quant-system/funnel"
URL = f"{BASE_URL}/funnel.html"
_DOCS = Path(__file__).resolve().parents[4] / "strategies" / "funnel_strategy" / "docs"
_BEIJING = timezone(timedelta(hours=8))
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _html_source(date: str | None = None) -> tuple[str, str]:
    """读取指定日期的历史报告；未指定则读最新报告。

    网络异常时回退到本地 docs 目录：指定日期取同名日期页，未指定取最近一次生成文件。
    """
    import time
    if date:
        url = f"{BASE_URL}/funnel-{date}.html"
        local = _DOCS / f"funnel-{date}.html"
    else:
        url = URL
        local = None
    url = f"{url}?v={int(time.time())}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", "replace"), url.split("?")[0]
    except Exception:
        if local and local.exists():
            return local.read_text(encoding="utf-8", errors="replace"), str(local)
        if not date:
            latest_page = _DOCS / "funnel.html"
            if latest_page.exists():
                return latest_page.read_text(encoding="utf-8", errors="replace"), str(latest_page)
            today = datetime.now(_BEIJING).date()
            files = []
            for p in _DOCS.glob("funnel-*.html"):
                m = re.search(r"funnel-(\d{4}-\d{2}-\d{2})\.html$", p.name)
                if m:
                    try:
                        files.append((datetime.strptime(m.group(1), "%Y-%m-%d").date(), p))
                    except ValueError:
                        pass
            if files:
                latest_date, latest = max(files, key=lambda x: x[0])
                if (today - latest_date).days <= 7:
                    return latest.read_text(encoding="utf-8", errors="replace"), str(latest)
        raise


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


@ttl_cache(300)
def funnel_dates() -> dict:
    """从主报告的日期选择器提取全部可用历史日期（升序返回，供前端下拉）。"""
    html, source = _html_source()
    dates = re.findall(r"var\s+available\s*=\s*\[([^\]]*)\]", html) or []
    found = re.findall(r"\"(\d{4}-\d{2}-\d{2})\"", dates[0]) if dates else []
    # 兜底：若内嵌数组缺失，扫描本地日期页文件名 + 最新报告日
    if not found:
        found = [m.group(1) for p in _DOCS.glob("funnel-*.html")
                 if (m := re.search(r"funnel-(\d{4}-\d{2}-\d{2})\.html$", p.name))]
    dates_sorted = sorted(set(found))
    return {"dates": dates_sorted, "latest": dates_sorted[-1] if dates_sorted else None, "source": source}


@ttl_cache(300)
def funnel_data(date: str | None = None) -> dict:
    """读取报告并结构化为 JSON；date 为 YYYY-MM-DD 时读对应历史页。"""
    if date and not _DATE_RE.match(str(date)):
        raise ValueError("日期格式须为 YYYY-MM-DD")
    html, source = _html_source(date)
    soup = BeautifulSoup(html, "lxml")
    title = _text(soup.find("h1")) or "五策略对比选股系统"
    date_input = soup.find("input", {"type": "date"})
    report_date = date_input.get("value") if date_input else None

    # 原页面的生成时刻与页面级说明、门控横幅一并透传，避免结构化解析丢内容
    generated = re.search(r"生成:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", soup.get_text(" ", strip=True))
    generated_at = generated.group(1) if generated else None
    page_note_ids = set()
    for section in soup.select("div.section"):
        page_note_ids.update(id(n) for n in section.select(".step-note"))
    page_notes = []
    for node in soup.select(".step-note"):
        if id(node) in page_note_ids:
            continue
        text = _text(node)
        if text and text not in page_notes:
            page_notes.append(text)
    banners = []
    for node in soup.find_all("div"):
        if "#fef2f2" not in (node.get("style") or "").lower():
            continue
        text = _text(node)
        if text and text.startswith("⚠️") and text not in banners:
            banners.append(text)
    html_url = f"{BASE_URL}/funnel-{date}.html" if date else URL

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
                "status": "no_candidates" if ("八步" in name and not candidates) else None,
                "steps": steps,
                "notes": [_text(n) for n in section.select(".step-note") if _text(n)],
                "banners": [
                    _text(n) for n in section.find_all("div")
                    if "#fef2f2" in (n.get("style") or "").lower() and _text(n).startswith("⚠️")
                ],
                "candidates": candidates,
                "candidate_count": len(candidates),
                "backtest": backtest,
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
        "generated_at": generated_at,
        "source": source,
        "html_url": html_url,
        "banners": banners,
        "notes": page_notes,
        # 完整原始 HTML 一并透传：前端「原版完整报告」视图用它做 srcdoc 兜底，
        # 保证 /recommend 与 funnel.html 的任何细节（含未来新增区块）都不丢失。
        "raw_html": html,
        "strategies": strategies,
    }
