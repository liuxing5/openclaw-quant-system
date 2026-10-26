# -*- coding: utf-8 -*-
"""回填 ``stock_announcements``（并购重组公告）。

数据源：巨潮资讯（CNINFO）公告接口，经 akshare ``stock_notice_report`` 封装。
一次取「某一天全市场公告」（约 1500~2000 条），本地按并购重组关键词过滤后
只把相关公告写入库，避免 30 万行级全量公告把慢链路压垮。

用法::

    python webapp/scripts/backfill_announcements.py --days 30
    python webapp/scripts/backfill_announcements.py --days 90 --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from psycopg2.extras import execute_values  # noqa: E402

from app.db import db_session  # noqa: E402

KEYWORDS = (
    "并购", "重组", "收购", "重大资产", "发行股份购买", "资产置换", "要约",
    "合并", "剥离", "出售资产", "股权转让", "控制权", "借壳", "资产注入",
    "吸收合并", "换股", "重组上市", "资产购买",
)
NOISE = ("减持", "质押", "担保", "回购注销", "业绩说明会", "股东大会通知")

SQL = """
INSERT INTO stock_announcements
    (ts_code, stock_name, title, category, publish_date, url, content_hash, source, fetched_at)
VALUES %s
ON CONFLICT (content_hash) DO NOTHING
"""


def _ts_code(code: str) -> str | None:
    c = (code or "").strip().zfill(6)
    if not c.isdigit():
        return None
    if c.startswith(("6", "9")):
        return f"{c}.SH"
    if c.startswith(("0", "3")):
        return f"{c}.SZ"
    if c.startswith(("4", "8", "920")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def _hash(ts_code: str, title: str, day: str) -> str:
    raw = f"{ts_code}|{title}|{day}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    try:
        import akshare as ak
    except Exception as exc:  # noqa: BLE001
        print(f"akshare 不可用: {exc}", flush=True)
        return 1

    today = date.today()
    days = [today - timedelta(days=i) for i in range(args.days)]
    days = [d for d in days if d.weekday() < 5]
    total_rows = 0
    total_matched = 0
    for day in sorted(days):
        ds = day.strftime("%Y%m%d")
        try:
            df = ak.stock_notice_report(symbol="全部", date=ds)
        except Exception as exc:  # noqa: BLE001
            print(f"{day} 抓取失败: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            continue
        if df is None or getattr(df, "empty", True) or "代码" not in getattr(df, "columns", []):
            print(f"{day} 无公告数据，跳过", flush=True)
            continue
        rows = []
        for _, r in df.iterrows():
            title = str(r.get("公告标题") or "").strip()
            category = str(r.get("公告类型") or "").strip()
            text = f"{title} {category}"
            if not any(k in text for k in KEYWORDS):
                continue
            if any(k in text for k in NOISE):
                continue
            code = _ts_code(str(r.get("代码") or ""))
            if not code:
                continue
            name = str(r.get("名称") or "").strip() or None
            day_str = str(r.get("公告日期") or day)[:10]
            rows.append(
                (
                    code,
                    name,
                    title[:1000],
                    category[:120] or "并购重组",
                    day_str,
                    str(r.get("网址") or "") or None,
                    _hash(code, title, day_str),
                    "CNINFO",
                    datetime.now(),
                )
            )
        total_rows += len(df)
        total_matched += len(rows)
        print(f"{day} 公告 {len(df)} 条，并购相关 {len(rows)} 条", flush=True)
        if rows and not args.dry_run:
            with db_session() as conn:
                cur = conn.cursor()
                try:
                    execute_values(cur, SQL, rows, page_size=100)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    cur.close()
        time.sleep(args.sleep)

    print(f"done: 扫描 {total_rows} 条，命中 {total_matched} 条，写入 {0 if args.dry_run else total_matched}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
