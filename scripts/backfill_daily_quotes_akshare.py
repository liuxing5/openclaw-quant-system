# -*- coding: utf-8 -*-
"""回填 daily_quotes 缺失交易日（akshare/东财源，仅补缺不覆盖）。

背景（2026-10-20）：trading_calendar.HOLIDAYS_2026 误将 2026-10-08/10-09 标为国庆
假期 → CI 例行抓取跳过两日 → daily_quotes 缺整天 → 下游 MA20/b20/A2 预筛/8step
K线窗口出现断档（8step 30 日历日窗口在缺行下雪上加霜，见收益改造报告·第二轮）。
日历已修正；本脚本回填缺失日数据。

口径（与 CI 主源 quotes.py 写入惯例对齐，且经 2026-10-16 库内行标定）：
  - 不复权价格（adjust=""，daily_quotes 存原始价）
  - --source em（东财 stock_zh_a_hist）: 成交量=手→×100 转股；pct/振幅/换手率列直取；
    pre_close = 收盘 - 涨跌额。注意：东财对高频/并发请求会断连（RemoteDisconnected），
    限流期间勿并发（--fetch-workers 1）。
  - --source sina（新浪 stock_zh_a_daily）: 成交量=股（精确值）；成交额=元；
    turnover=比例（×100→%）；无昨收列 → 拉目标日前移 12 自然日窗口自链
    （目标首日昨收=窗口内前一交易日收盘，衔接已实测通过）；无振幅列 → 后置补算。
  - pct_chg 两源均为 %（em 直取 2 位小数；sina 按 (close/昨收-1)×100 自算 4 位）。
  - 量比/振幅：入库后按 quotes._compute_*_sql 同公式、日期限定到回填范围补算
    （那两个函数只算"今天"，历史日必须用本脚本内的限定版）。
  - pe/pb/流通市值/大单净流等列库内本就 ~71% 覆盖，不回填（不猜口径）。
  - 已知局限：除权除息日按"前一交易日收盘"自链的昨收/涨幅与交易所除权口径有出入
    （占比极小；em 源同此局限）。

只补缺：INSERT ... ON CONFLICT (ts_code, trade_date) DO NOTHING，绝不覆盖已有行。

用法：
    python scripts/backfill_daily_quotes_akshare.py --start 20261008 --end 20261009 --limit 20    # 小规模验证
    python scripts/backfill_daily_quotes_akshare.py --start 20261008 --end 20261009 --source sina  # 全量（新浪）
    python scripts/backfill_daily_quotes_akshare.py --start 20261008 --end 20261009 --source em    # 全量（东财）
"""
import argparse
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import pandas as pd
from psycopg2.extras import execute_values

from core.db.connection import get_db_fresh
from core.utils.trading_calendar import is_trading_day

INSERT_SQL = """
    INSERT INTO daily_quotes (ts_code, trade_date, open, high, low, close,
                              volume, amount, pct_chg, turnover_rate, amplitude, pre_close)
    VALUES %s
    ON CONFLICT (ts_code, trade_date) DO NOTHING
"""

COLS = ["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额",
        "振幅", "涨跌幅", "涨跌额", "换手率"]


def _f(v):
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _target_dates(start, end):
    """范围内（按修正后的日历）交易日"""
    days = []
    d = start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def main():
    ap = argparse.ArgumentParser(description="回填 daily_quotes 缺失交易日（akshare/东财）")
    ap.add_argument("--start", required=True, help="起始日 YYYYMMDD 或 YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="结束日 YYYYMMDD 或 YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 只（0=全市场，验证用）")
    ap.add_argument("--throttle", type=float, default=0.12, help="每只请求间歇秒（防限流）")
    ap.add_argument("--source", choices=["em", "sina"], default="em",
                    help="行情源：em=东财历史K线；sina=新浪（详见文件头口径说明）。"
                         "东财限流期间用 sina")
    ap.add_argument("--fetch-workers", type=int, default=1,
                    help="并发拉取数。东财对并发连接直接断开（RemoteDisconnected），"
                         "实测 4 workers 即被拒；如 IP 环境宽松可尝试 2")
    ap.add_argument("--skip-aux-fill", action="store_true",
                    help="跳过入库后的量比/振幅限定补算")
    args = ap.parse_args()

    def _norm(s):
        return s.replace("-", "")

    start = datetime.strptime(_norm(args.start), "%Y%m%d").date()
    end = datetime.strptime(_norm(args.end), "%Y%m%d").date()
    targets = _target_dates(start, end)
    if not targets:
        print("范围内无交易日，退出")
        return 0
    print(f"回填范围: {start} ~ {end}，目标交易日: {[str(d) for d in targets]}")
    target_set = {str(d) for d in targets}

    conn = get_db_fresh()
    cur = conn.cursor()

    # 1. 股票宇宙 = 库内全部 A股 ts_code（历史行并集；区间内新上市、库中无任何
    #    历史行的股票不在宇宙内——该情形极罕见，见报告说明）
    _A_RE = re.compile(r"^(6\d{5}|00\d{4}|30\d{4}|43\d{4}|8[0-8]\d{4}|92\d{4})\.[A-Z]{2,3}$")
    cur.execute("SELECT DISTINCT ts_code FROM daily_quotes")
    universe = sorted(r[0] for r in cur.fetchall() if r[0] and _A_RE.match(r[0]))
    # 2. 已有全部目标日行的股票 → 跳过
    cur.execute("""
        SELECT ts_code, COUNT(DISTINCT trade_date)
        FROM daily_quotes
        WHERE trade_date BETWEEN %s AND %s
        GROUP BY ts_code
    """, (start, end))
    have = dict(cur.fetchall())
    full_todo = [c for c in universe if have.get(c, 0) < len(targets)]
    print(f"宇宙 {len(universe)} 只，已有完整行 {len(universe) - len(full_todo)}，"
          f"待回填 {len(full_todo)}")
    todo = full_todo[: args.limit] if args.limit else full_todo
    if not todo:
        print("✓ 已完整，无需回填")
        return 0

    import akshare as ak

    s_n, e_n = _norm(args.start), _norm(args.end)

    def _fetch_em(ts_code):
        """东财历史K线 → 12 列元组列表"""
        df = ak.stock_zh_a_hist(symbol=ts_code[:6], period="daily",
                                start_date=s_n, end_date=e_n, adjust="")
        rows = []
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                if str(r["日期"])[:10] not in target_set:
                    continue
                close = _f(r["收盘"])
                if close is None:
                    continue
                vol_hand = _f(r["成交量"])
                pct = _f(r["涨跌幅"])
                chg = _f(r["涨跌额"])
                preclose = None
                if chg is not None:
                    preclose = round(close - chg, 4)
                elif pct is not None:
                    preclose = round(close / (1 + pct / 100.0), 4)
                rows.append((
                    ts_code,
                    pd.to_datetime(str(r["日期"])).date(),
                    _f(r["开盘"]), _f(r["最高"]), _f(r["最低"]), close,
                    int(vol_hand * 100) if vol_hand is not None else None,  # 手→股
                    _f(r["成交额"]),
                    pct,
                    _f(r["换手率"]),
                    _f(r["振幅"]),
                    preclose,
                ))
        return rows

    def _fetch_sina(ts_code):
        """新浪日线 → 12 列元组列表。前移 12 自然日窗口自链昨收（新浪无 preclose 列）。"""
        num, exch = ts_code.split(".")
        sym = ("sh" if exch == "SH" else "sz" if exch == "SZ" else "bj") + num
        early = (start - timedelta(days=12)).strftime("%Y%m%d")
        df = ak.stock_zh_a_daily(symbol=sym, start_date=early, end_date=e_n, adjust="")
        rows = []
        prev_close = None
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                close = _f(r["close"])
                d10 = str(r["date"])[:10]
                if d10 in target_set and close is not None:
                    pct = (round((close / prev_close - 1) * 100, 4)
                           if prev_close else None)
                    turn = _f(r["turnover"])  # 新浪=比例(0.002=0.2%)
                    vol = _f(r["volume"])
                    rows.append((
                        ts_code,
                        datetime.fromisoformat(d10).date(),
                        _f(r["open"]), _f(r["high"]), _f(r["low"]), close,
                        int(round(vol)) if vol else None,  # 新浪量=股(精确值)
                        _f(r["amount"]),
                        pct,
                        round(turn * 100, 4) if turn is not None else None,
                        None,  # 振幅 → 后置补算
                        round(prev_close, 4) if prev_close else None,
                    ))
                if close is not None:
                    prev_close = close
        return rows

    def _fetch(ts_code):
        """单只拉取 → (ts_code, rows, err)；失败重试 1 次"""
        last_err = None
        for attempt in (1, 2):
            try:
                rows = (_fetch_sina(ts_code) if args.source == "sina"
                        else _fetch_em(ts_code))
                time.sleep(args.throttle)
                return ts_code, rows, None
            except Exception as e:
                last_err = e
                time.sleep(2 * attempt)
        return ts_code, None, str(last_err)[:160]

    buffer = []
    inserted = 0
    t0 = time.time()
    failed_codes = []
    consec_fail = 0

    def flush():
        nonlocal buffer, inserted
        if buffer:
            execute_values(cur, INSERT_SQL, buffer)
            conn.commit()
            inserted += len(buffer)
            buffer = []

    from concurrent.futures import ThreadPoolExecutor, as_completed

    workers = max(1, min(args.fetch_workers, len(todo)))
    print(f"并发拉取: source={args.source}, {workers} workers, "
          f"throttle={args.throttle}s/只/worker")
    aborted = False
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch, c): c for c in todo}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                _code, rows, err = fut.result()
            except Exception as e:  # noqa: BLE001
                rows, err = None, repr(e)
            if err is not None:
                failed_codes.append((futs[fut], err))
                consec_fail += 1
                if consec_fail >= 20:
                    print(f"\n❌ 连续 {consec_fail} 只失败（疑似限流），中止。"
                          f"已插入 {inserted} 条。末错误: {err}")
                    aborted = True
                    for f in futs:
                        f.cancel()
                    break
                continue
            consec_fail = 0
            if rows:
                buffer.extend(rows)
            if len(buffer) >= 1000:
                flush()
            if done % 200 == 0:
                print(f"  [{done}/{len(todo)}] 已插入 {inserted}（{time.time() - t0:.0f}s）"
                      f" 失败 {len(failed_codes)}")
    flush()
    if failed_codes:
        print(f"\n失败样例（前 10）:")
        for c, e in failed_codes[:10]:
            print(f"  {c}: {e}")
    print(f"\n{'⚠ 中止于限流，' if aborted else ''}拉取完成: 请求 {len(todo)}, "
          f"失败 {len(failed_codes)}, 插入 {inserted} 条, 用时 {time.time() - t0:.0f}s")

    # 3. 校验: 每日行数 + pre_close 衔接链
    print("\n== 校验 1: 各目标日行数 ==")
    for d in targets:
        cur.execute("SELECT COUNT(*) FROM daily_quotes WHERE trade_date = %s", (d,))
        n = cur.fetchone()[0]
        print(f"  {d}: {n}")

    print("\n== 校验 2: pre_close 衔接链（抽样 20 只）==")
    cur.execute("""
        SELECT ts_code FROM daily_quotes
        WHERE trade_date = %s AND pre_close IS NOT NULL
        ORDER BY random() LIMIT 20
    """, (targets[0],))
    samples = [r[0] for r in cur.fetchall()]
    ok = bad = 0
    for ts in samples:
        # 链: 区间内前一日 close == 后一日 pre_close（跨回填区间边界同样成立）
        cur.execute("""
            SELECT trade_date, close, pre_close FROM daily_quotes
            WHERE ts_code = %s AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """, (ts, start - timedelta(days=10), end + timedelta(days=10)))
        rows = cur.fetchall()
        for (d1, c1, _), (d2, _, pc2) in zip(rows, rows[1:]):
            if c1 is None or pc2 is None:
                continue
            if abs(float(c1) - float(pc2)) <= 0.011:  # 四舍五入容差
                ok += 1
            else:
                bad += 1
                print(f"  ✗ {ts} {d1} close={c1} → {d2} pre_close={pc2}")
    print(f"  衔接链: 通过 {ok}, 断裂 {bad}")

    # 4. 补算量比/振幅（公式同 core.market_data.quotes._compute_*_sql，日期限定到
    #    回填范围——那两个函数硬编码只算"今天"，历史日用本限定版）
    if not args.skip_aux_fill:
        vr_total = amp_total = 0
        for d in targets:
            cur.execute("""
                UPDATE daily_quotes dq
                SET volume_ratio = CASE WHEN sub.avg_vol > 0
                    THEN ROUND(dq.volume::numeric / sub.avg_vol, 2) END
                FROM (
                    SELECT ts_code, AVG(volume) AS avg_vol
                    FROM daily_quotes
                    WHERE trade_date < %s AND trade_date >= %s - INTERVAL '10 days'
                      AND volume IS NOT NULL AND volume > 0
                    GROUP BY ts_code HAVING COUNT(*) >= 3
                ) sub
                WHERE dq.ts_code = sub.ts_code AND dq.trade_date = %s
                  AND dq.volume IS NOT NULL AND dq.volume > 0
                  AND dq.volume_ratio IS NULL;
            """, (d, d, d))
            vr_total += cur.rowcount
            cur.execute("""
                UPDATE daily_quotes
                SET amplitude = ROUND(((high - low) / pre_close * 100)::numeric, 2)
                WHERE trade_date = %s AND high IS NOT NULL AND low IS NOT NULL
                  AND pre_close > 0 AND amplitude IS NULL;
            """, (d,))
            amp_total += cur.rowcount
        conn.commit()
        print(f"  补算（仅目标日 NULL 行）: volume_ratio {vr_total} 条, "
              f"amplitude {amp_total} 条")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
