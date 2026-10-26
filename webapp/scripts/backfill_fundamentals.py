# -*- coding: utf-8 -*-
"""回填 ``stock_fundamentals`` 多期财报（按报告期批量抓取，而不是逐只股票）。

数据源
------
东方财富 datacenter 的「按报告期全市场」接口，一期报告一次分页抓全市场：

* ``RPT_DMSK_FN_INCOME``   利润表：营收、归母净利、扣非净利、营业成本
* ``RPT_DMSK_FN_BALANCE``  资产负债表：总资产、总负债、净资产、资产负债率
* ``RPT_DMSK_FN_CASHFLOW`` 现金流量表：经营活动现金流净额
* ``RPT_LICO_FN_CPD``      业绩报表：EPS、BPS、加权 ROE、营收/净利同比、行业

一次报告期只需 4 个报表 × 约 12 页（500 行/页），比逐只股票 5000 次请求快两个数量级。
写入走 ``execute_values`` 批量 upsert，主键 ``(ts_code, report_date)`` 天然幂等。

用法::

    python webapp/scripts/backfill_fundamentals.py                 # 近 8 个季度
    python webapp/scripts/backfill_fundamentals.py --quarters 12
    python webapp/scripts/backfill_fundamentals.py --force         # 重抓已有报告期
    python webapp/scripts/backfill_fundamentals.py --dry-run       # 只抓不写
    python webapp/scripts/backfill_fundamentals.py --prune-stale   # 删除已被新数据覆盖的旧行

中断后重跑会跳过「该报告期已有 > 1000 行」的季度（--force 可强制重抓）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from functools import lru_cache

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from psycopg2.extras import execute_values  # noqa: E402

from app.db import db_session, query, scalar  # noqa: E402

BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

REPORTS = {
    "income": {
        "name": "RPT_DMSK_FN_INCOME",
        "date_col": "REPORT_DATE",
        "cols": "SECUCODE,REPORT_DATE,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT,"
        "DEDUCT_PARENT_NETPROFIT,OPERATE_COST",
    },
    "balance": {
        "name": "RPT_DMSK_FN_BALANCE",
        "date_col": "REPORT_DATE",
        "cols": "SECUCODE,REPORT_DATE,TOTAL_ASSETS,TOTAL_LIABILITIES,TOTAL_EQUITY,DEBT_ASSET_RATIO",
    },
    "cashflow": {
        "name": "RPT_DMSK_FN_CASHFLOW",
        "date_col": "REPORT_DATE",
        "cols": "SECUCODE,REPORT_DATE,NETCASH_OPERATE",
    },
    "indicator": {
        "name": "RPT_LICO_FN_CPD",
        "date_col": "REPORTDATE",
        "cols": "SECUCODE,REPORTDATE,BASIC_EPS,BPS,WEIGHTAVG_ROE,YSTZ,SJLTZ,BOARD_NAME,MGJYXJJE",
    },
}

FIN_INDUSTRIES = ("银行", "保险", "证券", "多元金融", "信托")

UPSERT = """
INSERT INTO stock_fundamentals (
    ts_code, report_date, revenue, net_profit, net_profit_deducted,
    gross_margin, net_margin, total_assets, total_liabilities, equity,
    debt_ratio, operating_cashflow, eps, bps, revenue_yoy, profit_yoy,
    industry
) VALUES %s
ON CONFLICT (ts_code, report_date) DO UPDATE SET
    revenue           = EXCLUDED.revenue,
    net_profit        = EXCLUDED.net_profit,
    net_profit_deducted = EXCLUDED.net_profit_deducted,
    gross_margin      = EXCLUDED.gross_margin,
    net_margin        = EXCLUDED.net_margin,
    total_assets      = EXCLUDED.total_assets,
    total_liabilities = EXCLUDED.total_liabilities,
    equity            = EXCLUDED.equity,
    debt_ratio        = EXCLUDED.debt_ratio,
    operating_cashflow = EXCLUDED.operating_cashflow,
    eps               = EXCLUDED.eps,
    bps               = EXCLUDED.bps,
    revenue_yoy       = EXCLUDED.revenue_yoy,
    profit_yoy        = EXCLUDED.profit_yoy,
    industry          = EXCLUDED.industry,
    fetched_at        = NOW()
"""


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _d(v) -> str | None:
    if not v:
        return None
    return str(v)[:10]


def last_quarter_ends(n: int) -> list[date]:
    """最近 n 个已结束季度末（含今天刚好是季末的情况），倒序。"""
    today = date.today()
    out: list[date] = []
    for year in range(today.year, today.year - 4, -1):
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            d = date(year, month, day)
            if d <= today:
                out.append(d)
                if len(out) >= n:
                    return sorted(out, reverse=True)
    return sorted(out, reverse=True)


def _api(params: dict, retries: int = 5) -> dict:
    url = BASE + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 域名固定
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"eastmoney request failed: {last}")


def fetch_report(key: str, day: date, page_size: int = 500) -> list[dict]:
    spec = REPORTS[key]
    ds = day.isoformat()
    rows: list[dict] = []
    page = 1
    while True:
        payload = _api(
            {
                "reportName": spec["name"],
                "columns": spec["cols"],
                "filter": f"({spec['date_col']}='{ds}')",
                "pageNumber": str(page),
                "pageSize": str(page_size),
                "source": "WEB",
                "client": "WEB",
            }
        )
        result = payload.get("result") or {}
        chunk = result.get("data") or []
        rows.extend(chunk)
        pages = int(result.get("pages") or 0)
        if len(chunk) < page_size or (pages and page >= pages):
            break
        page += 1
    print(f"  {key:9s} {ds} rows={len(rows)}", flush=True)
    return rows


@lru_cache(maxsize=1)
def known_codes() -> set[str]:
    return {r["ts_code"] for r in query("SELECT ts_code FROM stock_basic_info")}


def build_rows(day: date, stats: dict[str, list[dict]]) -> list[tuple]:
    """把四张报表按 SECUCODE 合成 stock_fundamentals 行。"""
    universe = known_codes()
    by_code: dict[str, dict] = {}
    for key in ("income", "balance", "cashflow", "indicator"):
        for r in stats.get(key) or []:
            code = (r.get("SECUCODE") or "").upper()
            if not code or code not in universe:
                continue
            item = by_code.setdefault(code, {})
            if key == "income":
                item["revenue"] = _f(r.get("TOTAL_OPERATE_INCOME"))
                item["net_profit"] = _f(r.get("PARENT_NETPROFIT"))
                item["net_profit_deducted"] = _f(r.get("DEDUCT_PARENT_NETPROFIT"))
                item["operate_cost"] = _f(r.get("OPERATE_COST"))
            elif key == "balance":
                item["total_assets"] = _f(r.get("TOTAL_ASSETS"))
                item["total_liabilities"] = _f(r.get("TOTAL_LIABILITIES"))
                item["equity"] = _f(r.get("TOTAL_EQUITY"))
                item["debt_ratio"] = _f(r.get("DEBT_ASSET_RATIO"))
            elif key == "cashflow":
                item["operating_cashflow"] = _f(r.get("NETCASH_OPERATE"))
            else:
                item["eps"] = _f(r.get("BASIC_EPS"))
                item["bps"] = _f(r.get("BPS"))
                item["revenue_yoy"] = _f(r.get("YSTZ"))
                item["profit_yoy"] = _f(r.get("SJLTZ"))
                item["industry"] = (r.get("BOARD_NAME") or "").strip() or None

    out: list[tuple] = []
    for code, x in by_code.items():
        revenue = x.get("revenue")
        net_profit = x.get("net_profit")
        cost = x.get("operate_cost")
        industry = x.get("industry")
        gross = None
        if revenue and revenue > 0 and cost is not None:
            if not industry or not any(k in industry for k in FIN_INDUSTRIES):
                gross = round((revenue - cost) / revenue * 100, 4)
        net_margin = round(net_profit / revenue * 100, 4) if revenue and revenue > 0 and net_profit is not None else None
        out.append(
            (
                code,
                day,
                revenue,
                net_profit,
                x.get("net_profit_deducted"),
                gross,
                net_margin,
                x.get("total_assets"),
                x.get("total_liabilities"),
                x.get("equity"),
                x.get("debt_ratio"),
                x.get("operating_cashflow"),
                x.get("eps"),
                x.get("bps"),
                x.get("revenue_yoy"),
                x.get("profit_yoy"),
                industry,
            )
        )
    return out


def upsert(rows: list[tuple]) -> int:
    if not rows:
        return 0
    with db_session() as conn:
        cur = conn.cursor()
        try:
            execute_values(cur, UPSERT, rows, page_size=200)
            conn.commit()
            return len(rows)
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def prune_stale(cutoff: date) -> int:
    """删掉已被新季度覆盖的远古行（如 600519 的 1998 年报）。"""
    with db_session() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM stock_fundamentals f WHERE f.report_date < %s "
            "AND EXISTS (SELECT 1 FROM stock_fundamentals n "
            "WHERE n.ts_code = f.ts_code AND n.report_date >= %s)",
            (cutoff, cutoff),
        )
        n = cur.rowcount
        conn.commit()
        cur.close()
    return n


def prune_unknown() -> int:
    """删掉不在 stock_basic_info 的代码（B 股 / 已退市 / 海外代码），保持表内口径统一。"""
    with db_session() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM stock_fundamentals f WHERE NOT EXISTS "
            "(SELECT 1 FROM stock_basic_info b WHERE b.ts_code = f.ts_code)"
        )
        n = cur.rowcount
        conn.commit()
        cur.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarters", type=int, default=8)
    ap.add_argument("--force", action="store_true", help="已有报告期也重抓")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune-stale", action="store_true", help="删除被新数据覆盖的旧报告期")
    ap.add_argument("--prune-unknown", action="store_true", help="删除不在 stock_basic_info 的代码")
    ap.add_argument("--page-size", type=int, default=500)
    args = ap.parse_args()

    days = last_quarter_ends(max(1, args.quarters))
    print("report dates:", ", ".join(d.isoformat() for d in days), flush=True)
    total_written = 0
    fetched_days: list[date] = []
    for day in days:
        existing = int(
            scalar(
                "SELECT COUNT(*) FROM stock_fundamentals WHERE report_date = %s",
                (day,),
            )
            or 0
        )
        if existing > 1000 and not args.force:
            print(f"skip {day} (existing {existing} rows)", flush=True)
            fetched_days.append(day)
            continue
        stats = {key: fetch_report(key, day, args.page_size) for key in REPORTS}
        rows = build_rows(day, stats)
        if args.dry_run:
            print(f"  dry-run {day}: would upsert {len(rows)} rows", flush=True)
        else:
            n = upsert(rows)
            total_written += n
            print(f"  upsert {day}: {n} rows", flush=True)
        fetched_days.append(day)

    if not args.dry_run and args.prune_stale and fetched_days:
        cutoff = min(fetched_days)
        n = prune_stale(cutoff)
        print(f"pruned {n} stale rows before {cutoff}", flush=True)
    if not args.dry_run and args.prune_unknown:
        n = prune_unknown()
        print(f"pruned {n} unknown-code rows", flush=True)

    print(f"done: written={total_written} dates={len(fetched_days)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
