# -*- coding: utf-8 -*-
"""同步概念/行业板块行情快照到 concept_board_quotes / industry_board_quotes。

数据源（均为免费无 Key）：
- 同花顺板块列表 + 代码：ak.stock_board_concept_name_ths / stock_board_industry_name_ths
- 概念资金流快照：ak.stock_fund_flow_concept('即时')   （含涨跌幅、公司家数、领涨股）
- 行业资金流快照：ak.stock_fund_flow_industry('即时')
- 行业板块明细：ak.stock_board_industry_summary_ths()  （涨跌幅/成交额/净流入/上涨下跌家数）

东财 push2 板块接口在本机网络下会被远端断开，因此不作为依赖；如恢复可用，
--with-em 会额外尝试补齐成分股到 concept_membership。

用法：
    python webapp/scripts/sync_boards.py
    python webapp/scripts/sync_boards.py --with-em
"""
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.utils.env import load_project_env  # noqa: E402

load_project_env()

import akshare as ak  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS concept_board_quotes (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    concept_code VARCHAR(20) NOT NULL,
    concept_name VARCHAR(100) NOT NULL,
    pct_chg FLOAT,
    turnover_rate FLOAT,
    lead_stock_code VARCHAR(20),
    lead_stock_name VARCHAR(50),
    stock_count INT,
    UNIQUE (trade_date, concept_code)
);
ALTER TABLE concept_board_quotes ADD COLUMN IF NOT EXISTS net_inflow FLOAT;
ALTER TABLE concept_board_quotes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
CREATE UNIQUE INDEX IF NOT EXISTS uq_concept_board
  ON concept_board_quotes (trade_date, concept_code);
CREATE TABLE IF NOT EXISTS industry_board_quotes (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    industry_code VARCHAR(20) NOT NULL,
    industry_name VARCHAR(100) NOT NULL,
    pct_chg FLOAT,
    turnover_rate FLOAT,
    lead_stock_code VARCHAR(20),
    lead_stock_name VARCHAR(50),
    stock_count INT,
    up_count INT,
    down_count INT,
    net_inflow FLOAT,
    amount FLOAT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (trade_date, industry_code)
);
CREATE TABLE IF NOT EXISTS board_member_cache (
    board_type VARCHAR(16) NOT NULL,
    board_code VARCHAR(20) NOT NULL,
    board_name VARCHAR(100) NOT NULL,
    ts_code VARCHAR(20) NOT NULL,
    stock_name VARCHAR(50),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (board_type, board_code, ts_code)
);
"""


def num(v, default=None):
    if v is None:
        return default
    try:
        if pd.isna(v):
            return default
    except Exception:  # noqa: BLE001
        pass
    if isinstance(v, str):
        v = v.replace("%", "").replace(",", "").replace("+", "").strip()
        if v in ("", "--", "-", "N/A"):
            return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def code_map(fn):
    """板块名 -> 板块代码（同花顺）"""
    try:
        df = fn()
        return {str(r["name"]).strip(): str(r["code"]).strip() for _, r in df.iterrows()}
    except Exception as e:  # noqa: BLE001
        print(f"  board code map failed: {type(e).__name__} {e}")
        return {}


def fetch_concepts():
    df = ak.stock_fund_flow_concept(symbol="即时")
    codes = code_map(ak.stock_board_concept_name_ths)
    today = date.today()
    rows = []
    for i, r in df.iterrows():
        name = str(r.get("行业", "")).strip()
        if not name:
            continue
        rows.append(
            (
                today,
                codes.get(name, f"THS{int(num(r.get('序号'), i) or 0):04d}"),
                name,
                num(r.get("行业-涨跌幅")),
                None,
                None,
                str(r.get("领涨股", "") or "").strip(),
                int(num(r.get("公司家数"), 0) or 0),
                num(r.get("净额")),
            )
        )
    return rows


def fetch_industries():
    """返回 10 元组：
    (trade_date, industry_code, industry_name, pct_chg, amount,
     lead_stock_name, stock_count, up_count, down_count, net_inflow)
    """
    today = date.today()
    codes = code_map(ak.stock_board_industry_name_ths)
    rows = []

    # 来源1：同花顺行业板块明细（字段最全）
    try:
        df = ak.stock_board_industry_summary_ths()
        for i, r in df.iterrows():
            name = str(r.get("板块", "")).strip()
            if not name:
                continue
            rows.append(
                (
                    today,
                    codes.get(name, f"THS{int(num(r.get('序号'), i) or 0):04d}"),
                    name,
                    num(r.get("涨跌幅")),
                    num(r.get("总成交额")),
                    str(r.get("领涨股", "") or "").strip(),
                    None,
                    int(num(r.get("上涨家数"), 0) or 0),
                    int(num(r.get("下跌家数"), 0) or 0),
                    num(r.get("净流入")),
                )
            )
        print(f"  industry summary_ths -> {len(rows)} rows")
    except Exception as e:  # noqa: BLE001
        print(f"  industry summary_ths failed: {type(e).__name__} {e}")

    # 来源2：行业资金流（补成分股家数）
    try:
        df = ak.stock_fund_flow_industry(symbol="即时")
        by_name = {str(r.get("行业", "")).strip(): r for _, r in df.iterrows()}
        merged = []
        for row in rows:
            r = by_name.get(row[2])
            if r is not None:
                row = list(row)
                if row[6] is None:
                    row[6] = int(num(r.get("公司家数"), 0) or 0)
                row = tuple(row)
            merged.append(row)
        if not rows:
            for i, r in df.iterrows():
                name = str(r.get("行业", "")).strip()
                merged.append(
                    (
                        today,
                        codes.get(name, f"THS{i:04d}"),
                        name,
                        num(r.get("行业-涨跌幅")),
                        None,
                        str(r.get("领涨股", "") or "").strip(),
                        int(num(r.get("公司家数"), 0) or 0),
                        None,
                        None,
                        num(r.get("净额")),
                    )
                )
        rows = merged
        print(f"  industry fund_flow merged -> {len(rows)} rows")
    except Exception as e:  # noqa: BLE001
        print(f"  industry fund_flow failed: {type(e).__name__} {e}")

    return rows


def try_em_members(concept_codes, limit=10):
    """东财成分股接口：本机当前会被远端断开，成功才写入。"""
    ok = 0
    for name, code in list(concept_codes.items())[:limit]:
        try:
            df = ak.stock_board_concept_cons_em(symbol=name)
            ok += 1
            yield code, name, df
            time.sleep(0.4)
        except Exception as e:  # noqa: BLE001
            print(f"  em cons fail {name}: {type(e).__name__}")
            return
    if ok == 0:
        print("  em member fetch skipped (source unreachable)")


def main():
    with_em = "--with-em" in sys.argv
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    for stmt in DDL.strip().split(";"):
        if stmt.strip():
            try:
                cur.execute(stmt)
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                print(f"  ddl skip: {type(e).__name__} {str(e)[:160]}")
    conn.commit()

    try:
        rows = fetch_concepts()
        cur.execute("DELETE FROM concept_board_quotes WHERE trade_date=%s", (date.today(),))
        execute_values(
            cur,
            """
            INSERT INTO concept_board_quotes
              (trade_date, concept_code, concept_name, pct_chg, turnover_rate,
               lead_stock_code, lead_stock_name, stock_count, net_inflow)
            VALUES %s
            """,
            rows,
            page_size=500,
        )
        conn.commit()
        print(f"  concept_board_quotes -> {len(rows)} rows")
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print(f"  concepts failed: {type(e).__name__} {e}")

    try:
        rows = fetch_industries()
        if rows:
            cur.execute("DELETE FROM industry_board_quotes WHERE trade_date=%s", (date.today(),))
            execute_values(
                cur,
                """
                INSERT INTO industry_board_quotes
                  (trade_date, industry_code, industry_name, pct_chg, amount,
                   lead_stock_name, stock_count, up_count, down_count, net_inflow)
                VALUES %s
                """,
                rows,
                page_size=500,
            )
            conn.commit()
            print(f"  industry_board_quotes -> {len(rows)} rows")
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print(f"  industries failed: {type(e).__name__} {e}")

    if with_em:
        concept_df = pd.DataFrame()
        try:
            concept_df = ak.stock_board_concept_name_em()
        except Exception:  # noqa: BLE001
            print("  em concept list unreachable, skip members")
        if not concept_df.empty:
            for _, r in concept_df.head(30).iterrows():
                try:
                    cons = ak.stock_board_concept_cons_em(symbol=str(r["名称"]))
                except Exception:  # noqa: BLE001
                    print("  em cons unreachable, stop")
                    break
                rows = [
                    ("concept", str(r["板块"]), str(r["名称"]), str(x["代码"]), str(x["名称"]))
                    for _, x in cons.iterrows()
                ]
                execute_values(
                    cur,
                    """
                    INSERT INTO board_member_cache
                      (board_type, board_code, board_name, ts_code, stock_name, updated_at)
                    VALUES %s
                    ON CONFLICT (board_type, board_code, ts_code) DO UPDATE
                      SET stock_name=EXCLUDED.stock_name, updated_at=NOW()
                    """,
                    rows,
                )
                conn.commit()
                time.sleep(0.5)

    cur.execute("SELECT COUNT(*) FROM concept_board_quotes WHERE trade_date=%s", (date.today(),))
    print("today concepts:", cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM industry_board_quotes WHERE trade_date=%s", (date.today(),))
    print("today industries:", cur.fetchone()[0])
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
