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

from core.db.connection import get_db_fresh

BEIJING_TZ = timezone(timedelta(hours=8))


def store_fundamentals(fundamentals: list) -> int:
    if not fundamentals:
        return 0

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        upserted = 0

        for item in fundamentals:
            try:
                cur.execute("SAVEPOINT sp_fund;")
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
                cur.execute("RELEASE SAVEPOINT sp_fund;")
                if cur.rowcount > 0:
                    upserted += 1
            except Exception as e:
                logger.debug(f"fundamentals upsert {item.get('ts_code')}: {e}")
                cur.execute("ROLLBACK TO SAVEPOINT sp_fund;")
                continue

        conn.commit()
        cur.close()
        logger.info(f"stock_fundamentals: upserted {upserted}/{len(fundamentals)}")
        return upserted
    except Exception as e:
        logger.error(f"store_fundamentals 失败: {e}")
        return 0
    finally:
        if conn and not conn.closed:
            conn.close()


def store_order_book(snapshots: list) -> int:
    if not snapshots:
        return 0

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        now = datetime.now(BEIJING_TZ)
        inserted = 0

        for item in snapshots:
            try:
                cur.execute("SAVEPOINT sp_ob;")
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
                cur.execute("RELEASE SAVEPOINT sp_ob;")
                if cur.rowcount > 0:
                    inserted += 1
            except Exception as e:
                logger.debug(f"order_book insert {item.get('ts_code')}: {e}")
                cur.execute("ROLLBACK TO SAVEPOINT sp_ob;")
                continue

        conn.commit()
        cur.close()
        logger.info(f"order_book_snapshot: inserted {inserted}/{len(snapshots)}")
        return inserted
    except Exception as e:
        logger.error(f"store_order_book 失败: {e}")
        return 0
    finally:
        if conn and not conn.closed:
            conn.close()


def store_announcements(announcements: list) -> int:
    if not announcements:
        return 0

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        inserted = 0

        for item in announcements:
            try:
                cur.execute("SAVEPOINT sp_ann;")
                title = item.get('title', '')
                content_hash = hashlib.md5(title.encode('utf-8')).hexdigest()
                cur.execute("""
                    INSERT INTO stock_announcements
                        (ts_code, stock_name, title, category, publish_date, url,
                         content_hash, source, fetched_at)
                    VALUES (%(ts_code)s, %(stock_name)s, %(title)s, %(category)s,
                            %(publish_date)s, %(url)s, %(content_hash)s, %(source)s, NOW())
                    ON CONFLICT (content_hash) DO NOTHING;
                """, {**item, 'content_hash': content_hash})
                cur.execute("RELEASE SAVEPOINT sp_ann;")
                if cur.rowcount > 0:
                    inserted += 1
            except Exception as e:
                logger.debug(f"announcements insert: {e}")
                cur.execute("ROLLBACK TO SAVEPOINT sp_ann;")
                continue

        conn.commit()
        cur.close()
        logger.info(f"stock_announcements: inserted {inserted}/{len(announcements)}")
        return inserted
    except Exception as e:
        logger.error(f"store_announcements 失败: {e}")
        return 0
    finally:
        if conn and not conn.closed:
            conn.close()


def update_tencent_quotes(tencent_data: list) -> int:
    if not tencent_data:
        return 0

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        updated = 0

        for item in tencent_data:
            try:
                cur.execute("SAVEPOINT sp_tx;")
                cur.execute("""
                    UPDATE daily_quotes SET
                        pe_ratio = %(pe_ratio)s,
                        pb_ratio = %(pb_ratio)s,
                        total_market_cap = %(total_market_cap)s,
                        circulating_market_cap = %(circulating_market_cap)s,
                        limit_up_price = %(limit_up_price)s,
                        limit_down_price = %(limit_down_price)s,
                        amplitude = %(amplitude)s,
                        volume_ratio = %(volume_ratio)s,
                        commission_ratio = %(commission_ratio)s,
                        large_order_net = %(large_order_net)s,
                        main_force_net = %(main_force_net)s
                    WHERE ts_code = %(ts_code)s
                      AND trade_date = (
                          SELECT MAX(trade_date) FROM daily_quotes
                          WHERE ts_code = %(ts_code)s
                      );
                """, item)
                cur.execute("RELEASE SAVEPOINT sp_tx;")
                if cur.rowcount > 0:
                    updated += 1
            except Exception as e:
                logger.debug(f"tencent update {item.get('ts_code')}: {e}")
                cur.execute("ROLLBACK TO SAVEPOINT sp_tx;")
                continue

        conn.commit()
        cur.close()
        logger.info(f"daily_quotes Tencent enrichment: updated {updated}/{len(tencent_data)}")
        return updated
    except Exception as e:
        logger.error(f"update_tencent_quotes 失败: {e}")
        return 0
    finally:
        if conn and not conn.closed:
            conn.close()


def store_strong_stock_rank(data: list) -> int:
    if not data:
        return 0

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        upserted = 0

        for item in data:
            try:
                cur.execute("SAVEPOINT sp_ssr;")
                cur.execute("""
                    INSERT INTO strong_stock_rank
                        (trade_date, ts_code, stock_name, rank_type, rank_position,
                         consecutive_days, stage_chg_pct, cumulative_turnover,
                         industry, latest_price, fetched_at)
                    VALUES (%(trade_date)s, %(ts_code)s, %(stock_name)s, %(rank_type)s,
                            %(rank_position)s, %(consecutive_days)s, %(stage_chg_pct)s,
                            %(cumulative_turnover)s, %(industry)s, %(latest_price)s, NOW())
                    ON CONFLICT (trade_date, ts_code, rank_type) DO UPDATE SET
                        rank_position = EXCLUDED.rank_position,
                        consecutive_days = EXCLUDED.consecutive_days,
                        stage_chg_pct = EXCLUDED.stage_chg_pct,
                        cumulative_turnover = EXCLUDED.cumulative_turnover,
                        industry = EXCLUDED.industry,
                        latest_price = EXCLUDED.latest_price,
                        fetched_at = NOW();
                """, item)
                cur.execute("RELEASE SAVEPOINT sp_ssr;")
                if cur.rowcount > 0:
                    upserted += 1
            except Exception as e:
                logger.debug(f"strong_stock_rank upsert {item.get('ts_code')}: {e}")
                cur.execute("ROLLBACK TO SAVEPOINT sp_ssr;")
                continue

        conn.commit()
        cur.close()
        logger.info(f"strong_stock_rank: upserted {upserted}/{len(data)}")
        return upserted
    except Exception as e:
        logger.error(f"store_strong_stock_rank 失败: {e}")
        return 0
    finally:
        if conn and not conn.closed:
            conn.close()


def store_earnings_forecast(data: list) -> int:
    if not data:
        return 0

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        upserted = 0

        for item in data:
            try:
                cur.execute("SAVEPOINT sp_ef;")
                cur.execute("""
                    INSERT INTO earnings_forecast
                        (ts_code, stock_name, forecast_year, institution_count,
                         eps_min, eps_mean, eps_max, industry_avg,
                         revenue_mean, profit_mean, fetched_at)
                    VALUES (%(ts_code)s, %(stock_name)s, %(forecast_year)s,
                            %(institution_count)s, %(eps_min)s, %(eps_mean)s,
                            %(eps_max)s, %(industry_avg)s, %(revenue_mean)s,
                            %(profit_mean)s, NOW())
                    ON CONFLICT (ts_code, forecast_year) DO UPDATE SET
                        stock_name = EXCLUDED.stock_name,
                        institution_count = EXCLUDED.institution_count,
                        eps_min = EXCLUDED.eps_min,
                        eps_mean = EXCLUDED.eps_mean,
                        eps_max = EXCLUDED.eps_max,
                        industry_avg = EXCLUDED.industry_avg,
                        revenue_mean = EXCLUDED.revenue_mean,
                        profit_mean = EXCLUDED.profit_mean,
                        fetched_at = NOW();
                """, item)
                cur.execute("RELEASE SAVEPOINT sp_ef;")
                if cur.rowcount > 0:
                    upserted += 1
            except Exception as e:
                logger.debug(f"earnings_forecast upsert {item.get('ts_code')}: {e}")
                cur.execute("ROLLBACK TO SAVEPOINT sp_ef;")
                continue

        conn.commit()
        cur.close()
        logger.info(f"earnings_forecast: upserted {upserted}/{len(data)}")
        return upserted
    except Exception as e:
        logger.error(f"store_earnings_forecast 失败: {e}")
        return 0
    finally:
        if conn and not conn.closed:
            conn.close()


def store_concept_board_quotes(data: list) -> int:
    if not data:
        return 0

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        upserted = 0

        for item in data:
            try:
                cur.execute("SAVEPOINT sp_cbq;")
                cur.execute("""
                    INSERT INTO concept_board_quotes
                        (trade_date, concept_code, concept_name, pct_chg,
                         turnover_rate, lead_stock_code, lead_stock_name,
                         stock_count, fetched_at)
                    VALUES (%(trade_date)s, %(concept_code)s, %(concept_name)s,
                            %(pct_chg)s, %(turnover_rate)s, %(lead_stock_code)s,
                            %(lead_stock_name)s, %(stock_count)s, NOW())
                    ON CONFLICT (trade_date, concept_code) DO UPDATE SET
                        concept_name = EXCLUDED.concept_name,
                        pct_chg = EXCLUDED.pct_chg,
                        turnover_rate = EXCLUDED.turnover_rate,
                        lead_stock_code = EXCLUDED.lead_stock_code,
                        lead_stock_name = EXCLUDED.lead_stock_name,
                        stock_count = EXCLUDED.stock_count,
                        fetched_at = NOW();
                """, item)
                cur.execute("RELEASE SAVEPOINT sp_cbq;")
                if cur.rowcount > 0:
                    upserted += 1
            except Exception as e:
                logger.debug(f"concept_board_quotes upsert {item.get('concept_code')}: {e}")
                cur.execute("ROLLBACK TO SAVEPOINT sp_cbq;")
                continue

        conn.commit()
        cur.close()
        logger.info(f"concept_board_quotes: upserted {upserted}/{len(data)}")
        return upserted
    except Exception as e:
        logger.error(f"store_concept_board_quotes 失败: {e}")
        return 0
    finally:
        if conn and not conn.closed:
            conn.close()


def store_concept_membership(data: list) -> int:
    if not data:
        return 0

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        upserted = 0

        for item in data:
            try:
                cur.execute("SAVEPOINT sp_cm;")
                cur.execute("""
                    INSERT INTO concept_membership
                        (ts_code, concept_code, concept_name, update_date)
                    VALUES (%(ts_code)s, %(concept_code)s, %(concept_name)s, %(update_date)s)
                    ON CONFLICT (ts_code, concept_code) DO UPDATE SET
                        concept_name = EXCLUDED.concept_name,
                        update_date = EXCLUDED.update_date;
                """, item)
                cur.execute("RELEASE SAVEPOINT sp_cm;")
                if cur.rowcount > 0:
                    upserted += 1
            except Exception as e:
                logger.debug(f"concept_membership upsert {item.get('ts_code')}: {e}")
                cur.execute("ROLLBACK TO SAVEPOINT sp_cm;")
                continue

        conn.commit()
        cur.close()
        logger.info(f"concept_membership: upserted {upserted}/{len(data)}")
        return upserted
    except Exception as e:
        logger.error(f"store_concept_membership 失败: {e}")
        return 0
    finally:
        if conn and not conn.closed:
            conn.close()


if __name__ == '__main__':
    logger.info("store_structured.py standalone: checking tables...")

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()

        for table in ['stock_fundamentals', 'order_book_snapshot', 'stock_announcements']:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = %s
                );
            """, (table,))
            exists = cur.fetchone()[0]
            logger.info(f"  {table}: {'exists' if exists else 'MISSING'}")

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

        cur.close()
    except Exception as e:
        logger.error(f"standalone check 失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()
    logger.info("Done.")
