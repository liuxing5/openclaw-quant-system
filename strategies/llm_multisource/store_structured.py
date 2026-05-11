"""Structured data storage for non-raw_signals tables.

Handles: stock_fundamentals, order_book_snapshot, stock_announcements,
and daily_quotes enrichment (PE/PB/market_cap from Tencent).
"""
import os
import sys
import hashlib
from datetime import datetime, timedelta, timezone
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.db.connection import get_db

BEIJING_TZ = timezone(timedelta(hours=8))


def store_fundamentals(fundamentals: list) -> int:
    """Bulk upsert into stock_fundamentals.

    Args:
        fundamentals: list of dicts with ts_code, report_date, revenue, net_profit, eps, etc.
    Returns:
        Number of rows upserted.
    """
    if not fundamentals:
        return 0

    conn = get_db()
    cur = conn.cursor()
    upserted = 0

    for item in fundamentals:
        try:
            cur.execute("""
                INSERT INTO stock_fundamentals
                    (ts_code, report_date, revenue, net_profit, eps, bps, fetched_at)
                VALUES (%(ts_code)s, %(report_date)s, %(revenue)s, %(net_profit)s,
                        %(eps)s, %(bps)s, NOW())
                ON CONFLICT (ts_code, report_date) DO UPDATE SET
                    revenue = EXCLUDED.revenue,
                    net_profit = EXCLUDED.net_profit,
                    eps = EXCLUDED.eps,
                    bps = EXCLUDED.bps,
                    fetched_at = NOW();
            """, item)
            if cur.rowcount > 0:
                upserted += 1
        except Exception as e:
            logger.debug(f"fundamentals upsert {item.get('ts_code')}: {e}")
            conn.rollback()
            continue

    conn.commit()
    cur.close(); conn.close()
    logger.info(f"stock_fundamentals: upserted {upserted}/{len(fundamentals)}")
    return upserted


def store_order_book(snapshots: list) -> int:
    """Bulk insert into order_book_snapshot.

    Args:
        snapshots: list of dicts with ts_code, bid/ask prices and volumes.
    Returns:
        Number of rows inserted.
    """
    if not snapshots:
        return 0

    conn = get_db()
    cur = conn.cursor()
    now = datetime.now(BEIJING_TZ)
    inserted = 0

    for item in snapshots:
        try:
            cur.execute("""
                INSERT INTO order_book_snapshot
                    (ts_code, snapshot_time,
                     bid1_price, bid1_vol, bid2_price, bid2_vol,
                     bid3_price, bid3_vol, bid4_price, bid4_vol,
                     bid5_price, bid5_vol,
                     ask1_price, ask1_vol, ask2_price, ask2_vol,
                     ask3_price, ask3_vol, ask4_price, ask4_vol,
                     ask5_price, ask5_vol)
                VALUES (%(ts_code)s, %(snapshot_time)s,
                        %(bid1_price)s, %(bid1_vol)s, %(bid2_price)s, %(bid2_vol)s,
                        %(bid3_price)s, %(bid3_vol)s, %(bid4_price)s, %(bid4_vol)s,
                        %(bid5_price)s, %(bid5_vol)s,
                        %(ask1_price)s, %(ask1_vol)s, %(ask2_price)s, %(ask2_vol)s,
                        %(ask3_price)s, %(ask3_vol)s, %(ask4_price)s, %(ask4_vol)s,
                        %(ask5_price)s, %(ask5_vol)s)
                ON CONFLICT (ts_code, snapshot_time) DO NOTHING;
            """, {**item, 'snapshot_time': now})
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            logger.debug(f"order_book insert {item.get('ts_code')}: {e}")
            conn.rollback()
            continue

    conn.commit()
    cur.close(); conn.close()
    logger.info(f"order_book_snapshot: inserted {inserted}/{len(snapshots)}")
    return inserted


def store_announcements(announcements: list) -> int:
    """Bulk insert into stock_announcements.

    Args:
        announcements: list of dicts with ts_code, title, category, publish_date, url, source.
    Returns:
        Number of rows inserted.
    """
    if not announcements:
        return 0

    conn = get_db()
    cur = conn.cursor()
    inserted = 0

    for item in announcements:
        try:
            title = item.get('title', '')
            content_hash = hashlib.md5(title.encode()).hexdigest()
            cur.execute("""
                INSERT INTO stock_announcements
                    (ts_code, stock_name, title, category, publish_date, url,
                     content_hash, source, fetched_at)
                VALUES (%(ts_code)s, %(stock_name)s, %(title)s, %(category)s,
                        %(publish_date)s, %(url)s, %(content_hash)s, %(source)s, NOW())
                ON CONFLICT (content_hash) DO NOTHING;
            """, {**item, 'content_hash': content_hash})
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            logger.debug(f"announcements insert: {e}")
            conn.rollback()
            continue

    conn.commit()
    cur.close(); conn.close()
    logger.info(f"stock_announcements: inserted {inserted}/{len(announcements)}")
    return inserted


def update_tencent_quotes(tencent_data: list) -> int:
    """UPDATE daily_quotes with PE/PB/market_cap from Tencent.

    Args:
        tencent_data: list of dicts with ts_code, pe_ratio, pb_ratio,
                      total_market_cap, circulating_market_cap,
                      limit_up_price, limit_down_price.
    Returns:
        Number of rows updated.
    """
    if not tencent_data:
        return 0

    conn = get_db()
    cur = conn.cursor()
    today = datetime.now(BEIJING_TZ).date()
    updated = 0

    for item in tencent_data:
        try:
            cur.execute("""
                UPDATE daily_quotes SET
                    pe_ratio = %(pe_ratio)s,
                    pb_ratio = %(pb_ratio)s,
                    total_market_cap = %(total_market_cap)s,
                    circulating_market_cap = %(circulating_market_cap)s,
                    limit_up_price = %(limit_up_price)s,
                    limit_down_price = %(limit_down_price)s
                WHERE ts_code = %(ts_code)s
                  AND trade_date = (
                      SELECT MAX(trade_date) FROM daily_quotes
                      WHERE ts_code = %(ts_code)s
                  );
            """, item)
            if cur.rowcount > 0:
                updated += 1
        except Exception as e:
            logger.debug(f"tencent update {item.get('ts_code')}: {e}")
            conn.rollback()
            continue

    conn.commit()
    cur.close(); conn.close()
    logger.info(f"daily_quotes Tencent enrichment: updated {updated}/{len(tencent_data)}")
    return updated


if __name__ == '__main__':
    """Standalone test: verify table creation and basic operations."""
    logger.info("store_structured.py standalone: checking tables...")

    conn = get_db()
    cur = conn.cursor()

    # Check tables exist
    for table in ['stock_fundamentals', 'order_book_snapshot', 'stock_announcements']:
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = %s
            );
        """, (table,))
        exists = cur.fetchone()[0]
        logger.info(f"  {table}: {'exists' if exists else 'MISSING'}")

    # Check daily_quotes new columns
    for col in ['pe_ratio', 'pb_ratio', 'total_market_cap', 'circulating_market_cap',
                'limit_up_price', 'limit_down_price']:
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns
                WHERE table_name = 'daily_quotes' AND column_name = %s
            );
        """, (col,))
        exists = cur.fetchone()[0]
        logger.info(f"  daily_quotes.{col}: {'exists' if exists else 'MISSING'}")

    cur.close(); conn.close()
    logger.info("Done.")
