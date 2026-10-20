# -*- coding: utf-8 -*-
"""回填 daily_quotes 缺失交易日（akshare/东财源，仅补缺不覆盖）。

背景（2026-10-20）：trading_calendar.HOLIDAYS_2026 误将 2026-10-08/10-09 标为国庆
假期 → CI 例行抓取跳过两日 → daily_quotes 缺整天 → 下游 MA20/b20/A2 预筛/8step
K线窗口出现断档（8step 30 日历日窗口在缺行下雪上加霜，见收益改造报告·第二轮）。
日历已修正；本脚本回填缺失日数据。

口径（与 CI 主源 quotes.py 写入惯例对齐，且经 2026-10-16 库内行标定）：
  - 不复权价格（adjust=""，daily_quotes 存原始价）
  - volume: EM 历史"成交量"= 手 → ×100 转 股（库口径=股）
  - amount = 元、pct_chg/振幅/换手率 = %（EM 列直取，与库内 EM 来源同口径）
  - pre_close = 收盘 - 涨跌额（精确昨收，衔接前一交易日收盘价）
  - pe/pb/流通市值/大单净流等列库内本就 ~71% 覆盖，不回填（不猜口径）
  - 量比在入库后由 quotes._compute_volume_ratio_sql() 补算（只补 NULL）

只补缺：INSERT ... ON CONFLICT (ts_code, trade_date) DO NOTHING，绝不覆盖已有行。

用法：
    python scripts/backfill_daily_quotes_akshare.py --start 20261008 --end 20261009 --limit 20   # 小规模验证
    python scripts/backfill_daily_quotes_akshare.py --start 20261008 --end 20261009              # 全量
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
    ap.add_argument("--fetch-workers", type=int, default=1,
                    help="并发拉取数。默认串行：东财对并发连接直接断开（RemoteDisconnected），"
                         "实测 4 workers 即被拒；如 IP 环境宽松可尝试 2")
    ap.add_argument("--no-volume-ratio", action="store_true", help="跳过入库后的 volume_ratio 补算")
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

    def _fetch(ts_code):
        """单只拉取 → (ts_code, rows, err)；失败重试 1 次"""
        last_err = None
        for attempt in (1, 2):
            try:
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
    print(f"并发拉取: {workers} workers, throttle={args.throttle}s/只/worker")
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

    # 4. 量比补算（只补 NULL）
    if not args.no_volume_ratio:
        try:
            from core.market_data.quotes import _compute_volume_ratio_sql
            _compute_volume_ratio_sql()
        except Exception as e:
            print(f"  volume_ratio 补算跳过: {e}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
