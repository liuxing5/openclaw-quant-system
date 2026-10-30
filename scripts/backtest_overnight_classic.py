# -*- coding: utf-8 -*-
"""最严格杨永兴版：半年逐日回测 + 次日 9:30-9:40 10 分钟 K 线。

- 信号日：日线原版八步过滤 + 5 分钟聚合出的 VWAP / 回踩 / 相对强弱
- 次日：9:30-9:40 的 5 分钟 K 线聚合为 10 分钟 K 线
- 成功：次日 9:30-9:40 最高价相对信号日 14:30 入场价涨幅 >= 阈值
- 回填：classic_backtest_trades / classic_backtest_daily / classic_backtest_10m

用法：
    python scripts/backtest_overnight_classic.py --months 6
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import baostock as bs
import psycopg2
from psycopg2.extras import execute_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webapp" / "backend"))

if not os.getenv("DATABASE_URL") and os.getenv("POSTGRES_HOST"):
    _u = quote(os.getenv("POSTGRES_USER", ""), safe="")
    _p = quote(os.getenv("POSTGRES_PASSWORD", ""), safe="")
    _url = (
        f"postgresql://{_u}:{_p}@{os.getenv('POSTGRES_HOST')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'postgres')}?sslmode=require"
    )
    os.environ["DATABASE_URL"] = _url
    os.environ["WEBAPP_DATABASE_URL"] = _url

from app.db import execute, query  # noqa: E402

THRESHOLDS = (0.01, 0.015, 0.02, 0.025, 0.03)
COST = 0.0025
_CACHE = None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bs_code(ts_code: str) -> str:
    return ("sh." if ts_code.endswith(".SH") else "sz.") + ts_code.split(".")[0]


def _index_code(ts_code: str) -> str:
    return "sh.000001" if ts_code.endswith(".SH") else "sz.399001"


def _fetch(code: str, start: str, end: str) -> list[dict]:
    global _CACHE
    if _CACHE is None:
        p = ROOT / "reports/yang_strict_bars.pkl"
        try:
            _CACHE = pickle.loads(p.read_bytes()) if p.exists() else {}
        except Exception:  # noqa: BLE001
            _CACHE = {}
    key = (code, start, end)
    if key in _CACHE:
        return _CACHE[key]
    rs = bs.query_history_k_data_plus(
        code,
        "date,time,open,high,low,close,volume,amount",
        start_date=start,
        end_date=end,
        frequency="5",
        adjustflag="2",
    )
    out = []
    while rs.error_code == "0" and rs.next():
        r = rs.get_row_data()
        try:
            out.append(
                {
                    "date": r[0],
                    "time": r[1][8:12],
                    "open": float(r[2]),
                    "high": float(r[3]),
                    "low": float(r[4]),
                    "close": float(r[5]),
                    "volume": float(r[6]),
                    "amount": float(r[7]),
                }
            )
        except (TypeError, ValueError):
            continue
    return out


def _ten_min(bars: list[dict]) -> list[dict]:
    """把 baostock 5m（09:35、09:40…）聚合成 10m 结束时间 09:40…10:30。"""
    groups: dict[str, list[dict]] = {}
    for b in bars:
        hh, mm = int(b["time"][:2]), int(b["time"][2:])
        minutes = hh * 60 + mm
        end_min = int(math.ceil(minutes / 10.0) * 10)
        key = f"{end_min // 60:02d}{end_min % 60:02d}"
        groups.setdefault(key, []).append(b)
    out = []
    for key in sorted(groups):
        g = groups[key]
        out.append(
            {
                "bar_time": f"{key[:2]}:{key[2:]}:00",
                "open": g[0]["open"],
                "high": max(x["high"] for x in g),
                "low": min(x["low"] for x in g),
                "close": g[-1]["close"],
                "volume": sum(x["volume"] for x in g),
                "amount": sum(x["amount"] for x in g),
            }
        )
    return out


def _daily_candidates(start: str, end: str) -> list[dict]:
    return query(
        """
        WITH base AS (
            SELECT q.ts_code, q.trade_date, q.close, q.high, q.volume, q.pct_chg,
                   q.turnover_rate, q.amount,
                   COALESCE(q.circulating_market_cap, q.total_market_cap) AS cap_base,
                   CASE
                       WHEN q.turnover_rate IS NOT NULL THEN q.turnover_rate
                       WHEN COALESCE(q.circulating_market_cap, q.total_market_cap) > 0
                       THEN q.amount / COALESCE(q.circulating_market_cap, q.total_market_cap) * 100.0
                   END AS turn_est,
                   AVG(q.volume) OVER (
                       PARTITION BY q.ts_code ORDER BY q.trade_date
                       ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
                   ) AS vma5,
                   EXISTS (
                       SELECT 1 FROM daily_quotes z
                       WHERE z.ts_code = q.ts_code
                         AND z.trade_date BETWEEN q.trade_date - INTERVAL '30 days'
                                              AND q.trade_date - INTERVAL '1 day'
                         AND z.pct_chg >= 9.5
                   ) AS had_limit_up
            FROM daily_quotes q
            WHERE q.trade_date BETWEEN %s::date - INTERVAL '70 days' AND %s::date
        ), feat AS (
            SELECT b.*,
                   COALESCE(b.cap_base, CASE WHEN b.turn_est > 0 THEN b.amount / (b.turn_est / 100.0) END) AS cap_est
            FROM base b
        )
        SELECT f.*, b.stock_name
        FROM feat f
        LEFT JOIN stock_basic_info b ON b.ts_code = f.ts_code
        WHERE f.trade_date BETWEEN %s::date AND %s::date
          AND (f.ts_code LIKE '60%%' OR f.ts_code LIKE '00%%')
          AND COALESCE(b.is_st, false) = false
          AND f.pct_chg BETWEEN 3 AND 5
          AND f.turn_est BETWEEN 5 AND 10
          AND f.cap_est BETWEEN 5e9 AND 2e10
          AND f.volume > f.vma5
          AND f.had_limit_up
        ORDER BY f.trade_date, f.ts_code
        """,
        (start, end, start, end),
    )


def _intraday(bars: list[dict], index_bars: list[dict]) -> dict | None:
    day_bars = [b for b in bars if b["time"] <= "1500"]
    if not day_bars:
        return None
    prior = [b for b in day_bars if b["time"] < "1430"]
    after = [b for b in day_bars if b["time"] >= "1430"]
    if not prior or not after:
        return None
    volume = sum(b["volume"] for b in day_bars)
    amount = sum(b["amount"] for b in day_bars)
    vwap = amount / volume if volume else 0
    entry = after[0]["close"]
    last = day_bars[-1]["close"]
    first = next((b for b in day_bars if b["time"] >= "0931"), day_bars[0])
    stock_ret = entry / first["open"] - 1 if first["open"] else 0
    idx_ret = 0
    if index_bars:
        ifirst = next((b for b in index_bars if b["time"] >= "0931"), index_bars[0])
        i1430 = [b for b in index_bars if b["time"] <= "1430"]
        if i1430 and ifirst["open"]:
            idx_ret = i1430[-1]["close"] / ifirst["open"] - 1
    new_high = max(b["high"] for b in after) > max(b["high"] for b in prior) * 1.0005
    pullback = min(b["low"] for b in after) <= vwap * 1.01 and last >= vwap * 0.995
    above = entry >= vwap and last >= vwap
    stronger = stock_ret > idx_ret
    return {
        "vwap": vwap,
        "entry": entry,
        "new_high": new_high,
        "pullback": pullback,
        "above_vwap": above,
        "stronger": stronger,
        "confirmed": bool(pullback and above and stronger),
        "stock_ret": stock_ret,
        "index_ret": idx_ret,
    }


def _init_schema():
    execute(
        """
        CREATE TABLE IF NOT EXISTS classic_backtest_trades (
            signal_date date NOT NULL,
            ts_code text NOT NULL,
            stock_name text,
            daily_pct numeric,
            turnover_rate numeric,
            cap_est numeric,
            entry_price numeric,
            vwap numeric,
            new_high boolean,
            pullback boolean,
            above_vwap boolean,
            stronger boolean,
            confirmed boolean,
            next_trade_date date,
            max_return numeric,
            close_1030 numeric,
            ret_1030 numeric,
            net_ret_1030 numeric,
            hit_1 boolean, hit_15 boolean, hit_2 boolean, hit_25 boolean, hit_3 boolean,
            ret_1 numeric, ret_15 numeric, ret_2 numeric, ret_25 numeric, ret_3 numeric,
            net_ret_1 numeric, net_ret_15 numeric, net_ret_2 numeric, net_ret_25 numeric, net_ret_3 numeric,
            created_at timestamptz DEFAULT now(),
            PRIMARY KEY (signal_date, ts_code)
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS classic_backtest_daily (
            trade_date date PRIMARY KEY,
            daily_candidates int,
            confirmed int,
            next_trade_date date,
            updated_at timestamptz DEFAULT now()
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS classic_backtest_10m (
            signal_date date NOT NULL,
            ts_code text NOT NULL,
            bar_time time NOT NULL,
            open numeric, high numeric, low numeric, close numeric,
            volume numeric, amount numeric,
            PRIMARY KEY (signal_date, ts_code, bar_time)
        )
        """
    )


def _upsert_trade(r: dict):
    cols = [
        "signal_date", "ts_code", "stock_name", "daily_pct", "turnover_rate", "cap_est",
        "entry_price", "vwap", "new_high", "pullback", "above_vwap", "stronger", "confirmed",
        "next_trade_date", "max_return", "close_1030", "ret_1030", "net_ret_1030",
        "hit_1", "hit_15", "hit_2", "hit_25", "hit_3",
        "ret_1", "ret_15", "ret_2", "ret_25", "ret_3",
        "net_ret_1", "net_ret_15", "net_ret_2", "net_ret_25", "net_ret_3",
    ]
    vals = [r.get(c) for c in cols]
    placeholders = ",".join(["%s"] * len(cols))
    execute(
        f"INSERT INTO classic_backtest_trades ({','.join(cols)}) VALUES ({placeholders}) "
        "ON CONFLICT (signal_date, ts_code) DO UPDATE SET "
        + ",".join(f"{c}=EXCLUDED.{c}" for c in cols[2:]),
        vals,
    )


def _upsert_daily(d: dict):
    execute(
        """INSERT INTO classic_backtest_daily (trade_date, daily_candidates, confirmed, next_trade_date)
           VALUES (%s,%s,%s,%s)
           ON CONFLICT (trade_date) DO UPDATE SET daily_candidates=EXCLUDED.daily_candidates,
             confirmed=EXCLUDED.confirmed, next_trade_date=EXCLUDED.next_trade_date, updated_at=now()""",
        (d["trade_date"], d["daily_candidates"], d["confirmed"], d["next_trade_date"]),
    )


def _insert_10m(sig_date: str, code: str, bars: list[dict]):
    for b in bars:
        execute(
            """INSERT INTO classic_backtest_10m
               (signal_date, ts_code, bar_time, open, high, low, close, volume, amount)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (signal_date, ts_code, bar_time) DO UPDATE SET
                 open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close,
                 volume=EXCLUDED.volume, amount=EXCLUDED.amount""",
            (
                sig_date, code, b["bar_time"], b["open"], b["high"], b["low"], b["close"],
                b["volume"], b["amount"],
            ),
        )


def _bulk_save(rows: list[dict], daily: list[dict], start: str, end: str):
    cols = [
        "signal_date", "ts_code", "stock_name", "daily_pct", "turnover_rate", "cap_est",
        "entry_price", "vwap", "new_high", "pullback", "above_vwap", "stronger", "confirmed",
        "next_trade_date", "max_return", "close_1030", "ret_1030", "net_ret_1030",
        "hit_1", "hit_15", "hit_2", "hit_25", "hit_3",
        "ret_1", "ret_15", "ret_2", "ret_25", "ret_3",
        "net_ret_1", "net_ret_15", "net_ret_2", "net_ret_25", "net_ret_3",
    ]
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("DELETE FROM classic_backtest_10m WHERE signal_date BETWEEN %s AND %s", (start, end))
    cur.execute("DELETE FROM classic_backtest_trades WHERE signal_date BETWEEN %s AND %s", (start, end))
    cur.execute("DELETE FROM classic_backtest_daily WHERE trade_date BETWEEN %s AND %s", (start, end))
    update = ",".join(f"{c}=EXCLUDED.{c}" for c in cols[2:])
    execute_values(
        cur,
        "INSERT INTO classic_backtest_trades (" + ",".join(cols) + ") VALUES %s "
        "ON CONFLICT (signal_date, ts_code) DO UPDATE SET " + update,
        [tuple(r.get(c) for c in cols) for r in rows],
        page_size=500,
    )
    bars = []
    for r in rows:
        for b in r.get("bars_10m") or []:
            bars.append(
                (r["signal_date"], r["ts_code"], b["bar_time"], b["open"], b["high"],
                 b["low"], b["close"], b["volume"], b["amount"])
            )
    execute_values(
        cur,
        "INSERT INTO classic_backtest_10m "
        "(signal_date,ts_code,bar_time,open,high,low,close,volume,amount) VALUES %s "
        "ON CONFLICT (signal_date,ts_code,bar_time) DO UPDATE SET "
        "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,"
        "volume=EXCLUDED.volume,amount=EXCLUDED.amount",
        bars,
        page_size=1000,
    )
    execute_values(
        cur,
        "INSERT INTO classic_backtest_daily (trade_date,daily_candidates,confirmed,next_trade_date) VALUES %s "
        "ON CONFLICT (trade_date) DO UPDATE SET daily_candidates=EXCLUDED.daily_candidates,"
        "confirmed=EXCLUDED.confirmed,next_trade_date=EXCLUDED.next_trade_date,updated_at=now()",
        [(d["trade_date"], d["daily_candidates"], d["confirmed"], d["next_trade_date"]) for d in daily],
    )
    conn.commit()
    cur.close()
    conn.close()


def _report(rows: list[dict], daily: list[dict], start: str, end: str, out_dir: Path):
    confirmed = [r for r in rows if r["confirmed"] and r["max_return"] is not None]
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("# 最严格杨永兴版 半年逐日回测报告")
    lines.append("")
    lines.append(f"- 回测区间：{start} ~ {end}")
    lines.append(f"- 交易日数：{len(daily)}")
    lines.append(f"- 日线候选：{len(rows)} 条；确认信号：{len(confirmed)} 条")
    lines.append(f"- 成功定义：次日 09:30-09:40 最高价相对信号日 14:30 入场价涨幅达到阈值")
    lines.append(f"- 成本：单边合计 {COST*100:.2f}%（佣金+印花税+滑点假设）")
    lines.append("- 10 分钟 K 线：由 baostock 5 分钟线聚合（09:40 第一根覆盖 09:30~09:40）")
    lines.append("")
    lines.append("## 总体成功概率")
    lines.append("")
    lines.append("| 阈值 | 命中数 | 成功概率 | 目标止盈策略均值 | 目标止盈总收益 | 9:40收盘均值 | 9:40收盘总收益 |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    for t in THRESHOLDS:
        key = str(t).replace(".", "_")
        hit = f"hit_{key.replace('0_','').replace('_0','')}"
        # 明确映射，避免浮点字符串歧义
        mapping = {0.01: "hit_1", 0.015: "hit_15", 0.02: "hit_2", 0.025: "hit_25", 0.03: "hit_3"}
        key = mapping[t]
        hits = sum(1 for r in confirmed if r.get(key))
        prob = hits / len(confirmed) if confirmed else 0
        rets = [r.get("net_ret_" + key[4:]) for r in confirmed]
        rets = [float(x) for x in rets if x is not None]
        closes = [float(r["net_ret_1030"]) for r in confirmed if r["net_ret_1030"] is not None]
        total = math.prod(1 + x for x in rets) - 1 if rets else 0
        ctotal = math.prod(1 + x for x in closes) - 1 if closes else 0
        lines.append(
            f"| {t*100:.1f}% | {hits}/{len(confirmed)} | {prob*100:.2f}% | "
            f"{sum(rets)/len(rets)*100 if rets else 0:.2f}% | {total*100:.2f}% | "
            f"{sum(closes)/len(closes)*100 if closes else 0:.2f}% | {ctotal*100:.2f}% |"
        )
    lines.append("")
    lines.append("## 按股票汇总（确认信号）")
    lines.append("")
    lines.append("| 代码 | 名称 | 次数 | >1% | >1.5% | >2% | >2.5% | >3% | 9:40均值 | 9:40总收益 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    by_code = {}
    for r in confirmed:
        by_code.setdefault(r["ts_code"], []).append(r)
    for code, items in sorted(by_code.items(), key=lambda kv: -len(kv[1])):
        name = items[0].get("stock_name") or ""
        n = len(items)
        rates = []
        for t in THRESHOLDS:
            mapping = {0.01: "hit_1", 0.015: "hit_15", 0.02: "hit_2", 0.025: "hit_25", 0.03: "hit_3"}
            rates.append(sum(1 for x in items if x.get(mapping[t])) / n * 100)
        closes = [float(x["net_ret_1030"]) for x in items if x["net_ret_1030"] is not None]
        total = math.prod(1 + x for x in closes) - 1 if closes else 0
        lines.append(
            f"| {code} | {name} | {n} | " + " | ".join(f"{x:.1f}%" for x in rates)
            + f" | {sum(closes)/len(closes)*100 if closes else 0:.2f}% | {total*100:.2f}% |"
        )
    lines.append("")
    lines.append("## 逐日候选数量")
    lines.append("")
    lines.append("| 日期 | 日线候选 | 确认 | 次日 |")
    lines.append("|---|---:|---:|---|")
    for d in daily:
        lines.append(f"| {d['trade_date']} | {d['daily_candidates']} | {d['confirmed']} | {d['next_trade_date'] or '—'} |")
    lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append("- 只有 `confirmed=True` 的标的进入成功概率统计；未确认标的保留在 `classic_backtest_trades` 供审计。")
    lines.append("- 成功概率按“次日 09:30-09:40 最高价是否达到阈值”计算，不等同于实际成交概率。")
    lines.append("- 目标止盈策略：达到阈值按阈值价卖出，否则按 9:40 收盘卖出；已扣除成本。")
    lines.append("- 历史 1 分钟数据不可得，使用 5 分钟线聚合的 10 分钟线；日内 VWAP 用 5 分钟成交额/成交量计算。")
    md = "\n".join(lines)
    (out_dir / "yang_strict_backtest.md").write_text(md, encoding="utf-8")
    (out_dir / "yang_strict_backtest.json").write_text(
        json.dumps({"rows": rows, "daily": daily}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    with (out_dir / "yang_strict_backtest_10m.csv").open("w", encoding="utf-8-sig", newline="") as f:
        f.write("signal_date,ts_code,bar_time,open,high,low,close,volume,amount\n")
        for r in rows:
            for b in r.get("bars_10m") or []:
                f.write(
                    f"{r['signal_date']},{r['ts_code']},{b['bar_time']},{b['open']},{b['high']},"
                    f"{b['low']},{b['close']},{b['volume']},{b['amount']}\n"
                )
    return confirmed, md


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=6)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--output", default=str(ROOT / "reports"))
    args = p.parse_args()
    end = args.end or date.today().isoformat()
    start = args.start or (date.today() - timedelta(days=args.months * 31)).isoformat()
    # 对齐到数据库实际交易日
    cal = [r["trade_date"].isoformat() for r in query("SELECT DISTINCT trade_date FROM daily_quotes WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date", (start, end))]
    if not cal:
        raise SystemExit("no daily data")
    start, end = cal[0], cal[-1]
    print(f"range {start} ~ {end}, {len(cal)} trading days", flush=True)
    _init_schema()
    candidates = _daily_candidates(start, end)
    print(f"daily candidates {len(candidates)}", flush=True)

    by_day: dict[str, list[dict]] = {}
    for c in candidates:
        by_day.setdefault(c["trade_date"].isoformat(), []).append(c)
    next_day = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}

    bs.login()
    idx_cache: dict[str, list[dict]] = {}
    rows = []
    daily_rows = []
    for i, d in enumerate(cal):
        items = by_day.get(d, [])
        confirmed_n = 0
        for c in items:
            nd = next_day.get(d)
            if not nd:
                continue
            s = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
            e = (datetime.strptime(nd, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
            bars = _fetch(_bs_code(c["ts_code"]), s, e)
            day_bars = [b for b in bars if b["date"] == d]
            nxt_bars = [b for b in bars if b["date"] == nd]
            idx_code = _index_code(c["ts_code"])
            if idx_code not in idx_cache:
                idx_cache[idx_code] = _fetch(idx_code, start, end)
            idx_bars = [b for b in idx_cache[idx_code] if b["date"] == d]
            intra = _intraday(day_bars, idx_bars)
            rec = {
                "signal_date": d,
                "ts_code": c["ts_code"],
                "stock_name": c.get("stock_name") or "",
                "daily_pct": _f(c["pct_chg"]),
                "turnover_rate": _f(c["turnover_rate"]),
                "cap_est": _f(c["cap_est"]),
                "entry_price": None,
                "vwap": None,
                "new_high": False,
                "pullback": False,
                "above_vwap": False,
                "stronger": False,
                "confirmed": False,
                "next_trade_date": nd,
                "max_return": None,
                "close_1030": None,
                "ret_1030": None,
                "net_ret_1030": None,
                "hit_1": False, "hit_15": False, "hit_2": False, "hit_25": False, "hit_3": False,
                "ret_1": None, "ret_15": None, "ret_2": None, "ret_25": None, "ret_3": None,
                "net_ret_1": None, "net_ret_15": None, "net_ret_2": None, "net_ret_25": None, "net_ret_3": None,
                "bars_10m": [],
            }
            if intra:
                rec.update(
                    {
                        "entry_price": intra["entry"],
                        "vwap": intra["vwap"],
                        "new_high": intra["new_high"],
                        "pullback": intra["pullback"],
                        "above_vwap": intra["above_vwap"],
                        "stronger": intra["stronger"],
                        "confirmed": intra["confirmed"],
                    }
                )
            ten = _ten_min([b for b in nxt_bars if "0930" <= b["time"] <= "1030"])
            rec["bars_10m"] = ten
            first = next((b for b in ten if b["bar_time"] == "09:40:00"), None)
            if first and rec["entry_price"]:
                high = first["high"]
                close = first["close"]
                max_ret = high / rec["entry_price"] - 1
                close_ret = close / rec["entry_price"] - 1
                rec["max_return"] = round(max_ret, 6)
                rec["close_1030"] = round(close, 3)
                rec["ret_1030"] = round(close_ret, 6)
                rec["net_ret_1030"] = round(close_ret - COST, 6)
                for t, key in zip(THRESHOLDS, ("1", "15", "2", "25", "3")):
                    hit = max_ret >= t
                    rec["hit_" + key] = hit
                    rec["ret_" + key] = round(t, 6) if hit else round(close_ret, 6)
                    rec["net_ret_" + key] = round(rec["ret_" + key] - COST, 6)
            rows.append(rec)
            if rec["confirmed"] and rec["max_return"] is not None:
                confirmed_n += 1
            if (len(rows) % 20) == 0:
                print(f"  {len(rows)}/{len(candidates)}", flush=True)
        daily_rows.append(
            {
                "trade_date": d,
                "daily_candidates": len(items),
                "confirmed": confirmed_n,
                "next_trade_date": next_day.get(d),
            }
        )
    bs.logout()
    _bulk_save(rows, daily_rows, start, end)

    confirmed, md = _report(rows, daily_rows, start, end, Path(args.output))
    print(f"confirmed {len(confirmed)} / candidates {len(rows)}", flush=True)
    print(md, flush=True)


if __name__ == "__main__":
    main()
