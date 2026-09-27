#!/usr/bin/env python3
"""Prepare the backtest report for GitHub Pages publishing.

What it does
------------
1. Injects a "last updated" banner (Asia/Shanghai time) into the report, so that
   daily regeneration is visible to anyone viewing the page.
2. Emits ``deploy/index.html``      -> clean root URL  (.../openclaw-quant-system/)
   and   ``deploy/backtest_report.html`` -> the canonical filename.
   Nothing else (notably not the large ``quotes_cache.pkl``) is published.
3. Copies auxiliary reports that happen to exist, avoiding hard failure.

Run:  .venv/Scripts/python.exe publish_pages.py
"""
import os
import re
import shutil
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(ROOT, "backtest", "backtest_report.html")
DEPLOY = os.path.join(ROOT, "deploy")
CST = timezone(timedelta(hours=8))

BANNER = (
    '<div style="background:#f8f9fa;border-bottom:1px solid #dee2e6;'
    'padding:10px 16px;text-align:center;color:#495057;'
    "font:13px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;\">"
    "最后更新：{ts}（北京时间）· 由 GitHub Actions 每日自动重新回测生成"
    "</div>"
)


def build_html(source_html: str, ts: str) -> str:
    """Insert the banner immediately after the opening <body> tag (once)."""
    banner = BANNER.format(ts=ts)
    if "<body>" in source_html:
        # Fast path: the report uses a bare <body>.
        return source_html.replace("<body>", "<body>\n" + banner, 1)
    # Fallback: honour an attributed <body ...> tag.
    return re.sub(r"<body[^>]*>",
                  lambda m: m.group(0) + "\n" + banner,
                  source_html,
                  count=1)


def main() -> None:
    if not os.path.exists(REPORT):
        raise SystemExit("report not found: %s (run backtest_revenue.py first)" % REPORT)

    html = open(REPORT, encoding="utf-8").read()
    ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    out_html = build_html(html, ts)

    # Always start from an empty deploy dir so removed files do not linger.
    if os.path.exists(DEPLOY):
        shutil.rmtree(DEPLOY)
    os.makedirs(DEPLOY)

    for name in ("index.html", "backtest_report.html"):
        with open(os.path.join(DEPLOY, name), "w", encoding="utf-8") as fh:
            fh.write(out_html)

    aux = os.path.join(ROOT, "backtest", "daily_recs_report.html")
    if os.path.exists(aux):
        shutil.copy(aux, os.path.join(DEPLOY, "daily_recs_report.html"))

    print("deploy ready -> %s" % DEPLOY)
    for f in sorted(os.listdir(DEPLOY)):
        print("  %-28s %8d bytes" % (f, os.path.getsize(os.path.join(DEPLOY, f))))
    print("updated %s (Asia/Shanghai)" % ts)


if __name__ == "__main__":
    main()
