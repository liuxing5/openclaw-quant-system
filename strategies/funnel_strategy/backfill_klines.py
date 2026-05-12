"""
全量历史K线回填
=================
使用 baostock 一次性拉取全市场 ~300 交易日 OHLCV 数据，
写入 daily_quotes（ON CONFLICT DO NOTHING，可断点续传）。

原理：
  - baostock.query_history_k_data_plus 单只股票一次查询全周期
  - 写入 daily_quotes，与每日行情管线共用一张表
  - daily_quotes 的 PRIMARY KEY (ts_code, trade_date) 保证幂等
  - 后续由 core/market_data/quotes.py 每日增量采集

参数：
  --days  回填天数（默认 350）
  --batch 每批写入条数（默认 500）
  --sleep 每只股票间休眠秒（默认 0.08s）
  --limit 限制股票数（调试用，默认无限制）

用法：
  # 全量回填 350 天
  python strategies/funnel_strategy/backfill_klines.py --days 350

  # 测试：只回填 10 只股票 30 天
  python strategies/funnel_strategy/backfill_klines.py --days 30 --limit 10

  # 补最新 5 天（日常补充用）
  python strategies/funnel_strategy/backfill_klines.py --days 5
"""
from __future__ import annotations

import sys
import os
import time
from datetime import date, datetime, timedelta, timezone

import baostock as bs
import pandas as pd
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db, db_configured
from core.utils.ts_code import standard_to_baostock
from core.utils.trading_calendar import is_trading_day as _is_trading_day
from dotenv import load_dotenv

for _env_path in ['.env', 'strategies/llm_multisource/.env']:
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
        break

BEIJING_TZ = timezone(timedelta(hours=8))

FIELDS = "date,open,high,low,close,volume,amount,turn,pctChg"


def _get_target_stocks() -> list:
    """从 daily_quotes 获取当前活跃股票列表（按最新交易日成交额降序）"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ts_code
        FROM daily_quotes
        ORDER BY ts_code
    """)
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return codes


def _fetch_stock_history(bs_code: str, start_date: str, end_date: str) -> list:
    """
    从 baostock 拉取单只股票历史K线。
    返回 [(trade_date, open, high, low, close, volume, amount, turnover_rate, pct_chg), ...]
    """
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            FIELDS,
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="3",  # 后复权
        )
        if rs.error_code != '0':
            return []

        rows = []
        while rs.next():
            row = rs.get_row_data()
            if not row or len(row) < 9:
                continue

            if row[0] is None or row[0] == '':
                continue

            try:
                trade_date = datetime.strptime(row[0], '%Y-%m-%d').date()
            except (ValueError, TypeError):
                continue

            def _f(val, default=None):
                if val is None or val == '':
                    return default
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default

            close = _f(row[4])
            if close is None or close <= 0:
                continue

            rows.append((
                trade_date,
                _f(row[1]),   # open
                _f(row[2]),   # high
                _f(row[3]),   # low
                close,         # close
                int(_f(row[5], 0)),  # volume
                _f(row[6], 0),  # amount
                _f(row[7], 0),  # turnover_rate (%)
                _f(row[8], 0),  # pct_chg (%)
            ))
        return rows
    except Exception as e:
        return []


def _insert_batch(ts_code: str, rows: list) -> int:
    """批量写入 daily_quotes（ON CONFLICT 跳过已有记录）"""
    if not rows:
        return 0

    conn = get_db()
    cur = conn.cursor()

    values = [(ts_code,) + r for r in rows]

    try:
        execute_values(cur, """
            INSERT INTO daily_quotes (
                ts_code, trade_date, open, high, low, close,
                volume, amount, turnover_rate, pct_chg
            ) VALUES %s
            ON CONFLICT (ts_code, trade_date) DO NOTHING
        """, values, page_size=500)
        inserted = cur.rowcount
        conn.commit()
        return inserted
    except Exception as e:
        conn.rollback()
        print(f"  ⚠️ {ts_code} 写入失败: {e}")
        return 0
    finally:
        cur.close()
        conn.close()


def run_backfill(
    days: int = 350,
    batch_size: int = 500,
    sleep_sec: float = 0.08,
    limit: int = 0,
    verbose: bool = True,
):
    """执行全量历史K线回填"""
    if not db_configured():
        print("❌ 数据库未配置")
        return

    end_date = date.today()
    start_date = end_date - timedelta(days=days * 2)

    print("=" * 60)
    print("  全量历史K线回填")
    print("=" * 60)
    print(f"  数据源: baostock (后复权)")
    print(f"  日期区间: {start_date} ~ {end_date}")
    print(f"  目标天数: {days}")
    print(f"  写入策略: ON CONFLICT DO NOTHING (可断点续传)")
    print("=" * 60)

    lg = bs.login()
    if lg.error_code != '0':
        print(f"❌ baostock 登录失败: {lg.error_msg}")
        return
    print(f"✓ baostock 登录成功")

    stocks = _get_target_stocks()
    if not stocks:
        print("❌ daily_quotes 无股票数据，请先运行每日行情采集")
        bs.logout()
        return

    if limit and limit > 0:
        stocks = stocks[:limit]

    print(f"✓ 待回填股票: {len(stocks)} 只")

    total_inserted = 0
    total_rows = 0
    failed_count = 0
    start_time = time.time()

    end_str = end_date.strftime('%Y-%m-%d')
    start_str = start_date.strftime('%Y-%m-%d')

    batch_buffer = {}

    for i, ts_code in enumerate(stocks):
        if verbose and (i + 1) % 200 == 0:
            elapsed = time.time() - start_time
            est_total = elapsed / (i + 1) * len(stocks)
            remaining = est_total - elapsed
            print(f"  进度: {i+1}/{len(stocks)}  "
                  f"已写入{total_inserted}条  "
                  f"剩余≈{remaining/60:.0f}min  "
                  f"失败{failed_count}只")

        bs_code = standard_to_baostock(ts_code)
        rows = _fetch_stock_history(bs_code, start_str, end_str)

        if not rows:
            failed_count += 1
            continue

        batch_buffer[ts_code] = rows
        total_rows += len(rows)

        if len(batch_buffer) >= batch_size:
            for code, data in batch_buffer.items():
                total_inserted += _insert_batch(code, data)
            batch_buffer.clear()

        time.sleep(sleep_sec)

    # 最后一批
    if batch_buffer:
        for code, data in batch_buffer.items():
            total_inserted += _insert_batch(code, data)

    bs.logout()

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"  ✅ 回填完成")
    print(f"  耗时: {elapsed/60:.1f} 分钟")
    print(f"  成功股票: {len(stocks) - failed_count} 只")
    print(f"  失败股票: {failed_count} 只")
    print(f"  总K线条数: {total_rows}")
    print(f"  实际写入: {total_inserted} 条 (跳过{total_rows - total_inserted}条已存在)")
    print(f"{'='*60}")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='全量历史K线回填')
    parser.add_argument('--days', type=int, default=350,
                        help='回填天数 (default: 350)')
    parser.add_argument('--batch', type=int, default=500,
                        help='每批写入股票数 (default: 500)')
    parser.add_argument('--sleep', type=float, default=0.08,
                        help='每只股票间休眠秒 (default: 0.08)')
    parser.add_argument('--limit', type=int, default=0,
                        help='限制股票数 (0=全量, 调试用)')

    args = parser.parse_args()
    run_backfill(
        days=args.days,
        batch_size=args.batch,
        sleep_sec=args.sleep,
        limit=args.limit,
    )
