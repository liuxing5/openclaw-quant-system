#!/usr/bin/env python3
"""ST 历史区间回填（幸存者偏差基础设施）
====================================
用 baostock query_history_k_data_plus 的 isST 字段，识别每只股票在回填区间内
被实施特别处理（ST/*ST）的连续交易日区间，写入 stock_st_history 表。

表结构（幂等建表）：
    stock_st_history(ts_code, st_name, start_date, end_date)
    PRIMARY KEY (ts_code, start_date)

说明/限制：
  - baostock 的 isST 只给出"当日是否 ST"，不提供当时的 ST 名称（如 *ST 新亿）。
    st_name 用 stock_basic_info.stock_name（当前名）作为尽力而为的标签，
    真正承载幸存者偏差信息的是 (ts_code, start_date, end_date) 区间本身。
  - end_date 为该 ST 区间最后一个交易日（含）。若区间一直延续到回填区间末尾，
    end_date 取回填区间结束日。
  - 幂等：以 (ts_code, start_date) 为主键 ON CONFLICT 更新，可重复运行/断点续传。

用法：
    # 全市场回填 2024-01-01 至今
    python strategies/shared/backfill_st_history.py

    # 指定区间 / 限流 / 调试
    python strategies/shared/backfill_st_history.py --start-date 2025-01-01 --sleep 0.15
    python strategies/shared/backfill_st_history.py --limit 20

    # 只回填 stock_basic_info 中当前带 ST 标记的股票（快速子集）
    python strategies/shared/backfill_st_history.py --only-current-st
"""
from __future__ import annotations

import os
import sys
import time
import socket
from datetime import date, datetime, timedelta, timezone

import baostock as bs

# 防止 baostock 网络请求卡死
socket.setdefaulttimeout(20)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db_fresh, db_configured
from core.utils.ts_code import standard_to_baostock, baostock_to_standard
from dotenv import load_dotenv

# 加载仓库根 .env（无论从哪个 cwd 运行）
for _env_path in [
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '.env')),
    '.env',
]:
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
        break

BEIJING_TZ = timezone(timedelta(hours=8))

# 查询字段：仅需日期与 ST 标记
FIELDS = "date,isST"


def _ensure_table(conn):
    """幂等建表 stock_st_history。"""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_st_history (
            ts_code VARCHAR(20) NOT NULL,
            st_name VARCHAR(50),
            start_date DATE NOT NULL,
            end_date DATE,
            PRIMARY KEY (ts_code, start_date)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_st_hist_code ON stock_st_history(ts_code);
    """)
    conn.commit()
    cur.close()


def _get_target_stocks(only_current_st: bool) -> list:
    """从 stock_basic_info 取待回填股票 [(ts_code, stock_name), ...]。

    默认包含退市股（不过滤 is_active）——幸存者偏差要求覆盖历史 ST 期间
    已退市的股票。
    """
    conn = None
    try:
        conn = _connect_with_retry()
        cur = conn.cursor()
        if only_current_st:
            cur.execute("""
                SELECT ts_code, stock_name
                FROM stock_basic_info
                WHERE is_st = TRUE
                ORDER BY ts_code
            """)
        else:
            cur.execute("""
                SELECT ts_code, stock_name
                FROM stock_basic_info
                ORDER BY ts_code
            """)
        rows = [(r[0], r[1]) for r in cur.fetchall()]
        cur.close()
        return rows
    except Exception as e:
        print(f"⚠️ 获取股票列表失败: {e}")
        return []
    finally:
        if conn and not conn.closed:
            conn.close()


def _connect_with_retry(attempts: int = 6, base_delay: float = 3.0):
    """带重试的连接获取（Supabase pooler 偶发 connection refused）。"""
    last_exc = None
    for i in range(attempts):
        try:
            return get_db_fresh()
        except Exception as e:  # noqa: BLE001
            last_exc = e
            wait = base_delay * (i + 1)
            print(f"⚠️ 数据库连接失败({i+1}/{attempts}), {wait:.0f}s 后重试: {e}")
            time.sleep(wait)
    raise last_exc


def _st_intervals_from_flags(date_flags) -> list:
    """把 [(date, isST)] 顺序序列压缩为连续 ST 区间 [(start_date, end_date), ...]。

    isST=='1' 视为 ST。区间按交易日连续（baostock 只返回交易日，
    周末/节假日自然跳过，不影响连续性判定）。

    2026-10-14 审查修复: ST run 覆盖到数据最后一天时 end_date 置 NULL
    （进行中），否则区间被截断在末日，次日 is_st_mask 会漏判。
    """
    if not date_flags:
        return []
    last_data_date = date_flags[-1][0]
    intervals = []
    run_start = None
    last_st_date = None
    for d, is_st in date_flags:
        if is_st == '1':
            if run_start is None:
                run_start = d
            last_st_date = d
        else:
            if run_start is not None:
                intervals.append((run_start, last_st_date))
                run_start = None
                last_st_date = None
    if run_start is not None:
        # run 延续到数据末日 → 进行中，end_date=NULL
        end = None if last_st_date == last_data_date else last_st_date
        intervals.append((run_start, end))
    return intervals


def _fetch_st_flags(bs_code: str, start_date: str, end_date: str) -> list:
    """查询单只股票区间内每日 isST，返回 [(date, isST), ...]。

    2026-10-14 审查修复: 失败返回 None（调用方计为失败并跳过），
    空列表仅表示该股确实无数据；此前失败也返回 []，会被误当成
    "无 ST"静默写偏。瞬时网络失败自动重试 2 次。
    """
    for attempt in range(3):
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                FIELDS,
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3",
            )
            if rs.error_code != '0':
                raise RuntimeError(f"error_code={rs.error_code}")
            out = []
            while rs.next():
                row = rs.get_row_data()
                if not row or len(row) < 2:
                    continue
                d_raw, is_st = row[0], row[1]
                if not d_raw:
                    continue
                try:
                    d = datetime.strptime(d_raw, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    continue
                out.append((d, is_st))
            return out
        except Exception:  # noqa: BLE001 — 网络/解析失败统一重试
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None
    return None


def _insert_intervals(ts_code: str, st_name: str, intervals: list) -> int:
    if not intervals:
        return 0
    conn = None
    try:
        conn = _connect_with_retry()
        cur = conn.cursor()
        values = [(ts_code, st_name, s, e) for (s, e) in intervals]
        from psycopg2.extras import execute_values
        execute_values(cur, """
            INSERT INTO stock_st_history (ts_code, st_name, start_date, end_date)
            VALUES %s
            ON CONFLICT (ts_code, start_date) DO UPDATE SET
                st_name=EXCLUDED.st_name,
                end_date=EXCLUDED.end_date
        """, values, page_size=200)
        inserted = cur.rowcount
        conn.commit()
        cur.close()
        return inserted
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"  ⚠️ {ts_code} 写入失败: {e}")
        return 0
    finally:
        if conn and not conn.closed:
            conn.close()


def run_backfill(
    start_date: str = None,
    end_date: str = None,
    sleep_sec: float = 0.12,
    limit: int = 0,
    only_current_st: bool = False,
    shard: str = '',
    verbose: bool = True,
):
    if not db_configured():
        print("❌ 数据库未配置（请在仓库根 .env 设置 POSTGRES_*）")
        return

    end_dt = (datetime.now(BEIJING_TZ).date() if end_date
              else datetime.now(BEIJING_TZ).date())
    start_dt = (datetime.strptime(start_date, '%Y-%m-%d').date() if start_date
                else date(2024, 1, 1))

    print("=" * 60)
    print("  ST 历史区间回填 (stock_st_history)")
    print("=" * 60)
    print(f"  数据源: baostock isST")
    print(f"  日期区间: {start_dt} ~ {end_dt}")
    print(f"  股票范围: {'当前带ST标记' if only_current_st else '全市场(含退市)'}")
    print(f"  写入策略: ON CONFLICT 更新 (幂等/可续传)")
    print("=" * 60)

    # 建表
    conn = _connect_with_retry()
    _ensure_table(conn)
    if not conn.closed:
        conn.close()

    stocks = _get_target_stocks(only_current_st)
    if not stocks:
        print("❌ 无股票可回填（stock_basic_info 为空？）")
        return
    if limit and limit > 0:
        stocks = stocks[:limit]

    # 分片（并行回填用）: --shard i/n，只处理 i == idx % n 的股票
    if shard:
        try:
            _si, _sn = (int(x) for x in shard.split('/'))
            stocks = [s for j, s in enumerate(stocks) if j % _sn == _si]
            print(f"✓ 分片 {shard}: 本片 {len(stocks)} 只")
        except ValueError:
            print(f"⚠️ --shard 格式错误（应为 i/n）: {shard}，忽略分片")

    print(f"✓ 待处理股票: {len(stocks)} 只")

    lg = bs.login()
    if lg.error_code != '0':
        print(f"❌ baostock 登录失败: {lg.error_msg}")
        return
    print("✓ baostock 登录成功")

    start_str = start_dt.strftime('%Y-%m-%d')
    end_str = end_dt.strftime('%Y-%m-%d')

    total_intervals = 0
    st_stock_count = 0
    failed_count = 0
    start_time = time.time()

    try:
        for i, (ts_code, st_name) in enumerate(stocks):
            if verbose and (i + 1) % 100 == 0:
                elapsed = time.time() - start_time
                est_total = elapsed / (i + 1) * len(stocks)
                remaining = est_total - elapsed
                print(f"  进度: {i+1}/{len(stocks)}  ST区间{total_intervals}  "
                      f"含ST股票{st_stock_count}  剩余≈{remaining/60:.0f}min  "
                      f"失败{failed_count}")

            bs_code = standard_to_baostock(ts_code)
            flags = _fetch_st_flags(bs_code, start_str, end_str)
            if flags is None:
                failed_count += 1
                continue

            intervals = _st_intervals_from_flags(flags)
            if intervals:
                n = _insert_intervals(ts_code, st_name or ts_code, intervals)
                total_intervals += n
                st_stock_count += 1

            time.sleep(sleep_sec)
    finally:
        bs.logout()

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"  ✅ 回填完成")
    print(f"  耗时: {elapsed/60:.1f} 分钟")
    print(f"  处理股票: {len(stocks)} 只 (失败 {failed_count})")
    print(f"  含ST区间股票: {st_stock_count} 只")
    print(f"  写入ST区间: {total_intervals} 条")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='ST 历史区间回填')
    parser.add_argument('--start-date', type=str, default=None,
                        help='开始日期 YYYY-MM-DD (默认 2024-01-01)')
    parser.add_argument('--end-date', type=str, default=None,
                        help='结束日期 YYYY-MM-DD (默认今天)')
    parser.add_argument('--sleep', type=float, default=0.12,
                        help='每只股票间休眠秒 (default: 0.12, baostock 限速)')
    parser.add_argument('--limit', type=int, default=0,
                        help='限制股票数 (0=全量, 调试用)')
    parser.add_argument('--only-current-st', action='store_true',
                        help='只回填 stock_basic_info 当前 is_st=TRUE 的股票（快速）')
    parser.add_argument('--shard', type=str, default='',
                        help='分片 i/n（并行回填，如 0/6 1/6 ...）')

    args = parser.parse_args()
    run_backfill(
        start_date=args.start_date,
        end_date=args.end_date,
        sleep_sec=args.sleep,
        limit=args.limit,
        only_current_st=args.only_current_st,
        shard=args.shard,
    )
