"""AKShare 行情数据每日采集"""
import os
import time
from datetime import datetime, date
import akshare as ak
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from loguru import logger
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), '.env'))


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'), user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'), dbname=os.getenv('POSTGRES_DB'),
    )


def ensure_market_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS daily_quotes (
        ts_code VARCHAR(20),
        trade_date DATE,
        open NUMERIC(10,3), high NUMERIC(10,3), low NUMERIC(10,3), close NUMERIC(10,3),
        volume BIGINT, amount NUMERIC(20,2),
        pct_chg FLOAT, turnover_rate FLOAT,
        PRIMARY KEY (ts_code, trade_date)
    );
    CREATE TABLE IF NOT EXISTS lhb_detail (
        id BIGSERIAL PRIMARY KEY,
        trade_date DATE, ts_code VARCHAR(20), stock_name VARCHAR(50),
        reason TEXT, buy_amt NUMERIC(20,2), sell_amt NUMERIC(20,2),
        net_amt NUMERIC(20,2), is_inst BOOLEAN
    );
    CREATE INDEX IF NOT EXISTS idx_lhb_date ON lhb_detail(trade_date DESC, ts_code);
    
    CREATE TABLE IF NOT EXISTS hsgt_individual (
        ts_code VARCHAR(20), trade_date DATE,
        hold_shares BIGINT, hold_market_cap NUMERIC(20,2),
        net_buy_amount NUMERIC(20,2),
        PRIMARY KEY (ts_code, trade_date)
    );
    
    CREATE TABLE IF NOT EXISTS concept_membership (
        ts_code VARCHAR(20), concept_code VARCHAR(20), concept_name VARCHAR(100),
        update_date DATE, PRIMARY KEY (ts_code, concept_code)
    );
    """
    conn = get_db(); cur = conn.cursor()
    cur.execute(sql); conn.commit(); cur.close(); conn.close()


def fetch_daily_quotes_today():
    """全市场当日行情快照"""
    try:
        df = ak.stock_zh_a_spot_em()
    except Exception as e:
        logger.warning(f"daily_quotes skipped (海外服务器无法访问东方财富): {e}")
        return
    today = date.today()
    rows = []
    for _, r in df.iterrows():
        code = str(r['代码']).zfill(6)
        ts = code + ('.SH' if code.startswith('6') else '.SZ')
        rows.append((
            ts, today,
            r.get('今开'), r.get('最高'), r.get('最低'), r.get('最新价'),
            r.get('成交量'), r.get('成交额'),
            r.get('涨跌幅'), r.get('换手率'),
        ))
    
    conn = get_db(); cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO daily_quotes VALUES %s
        ON CONFLICT (ts_code, trade_date) DO UPDATE SET
        close=EXCLUDED.close, pct_chg=EXCLUDED.pct_chg,
        volume=EXCLUDED.volume, amount=EXCLUDED.amount;
    """, rows)
    conn.commit()
    logger.info(f"daily_quotes: {len(rows)} rows")
    cur.close(); conn.close()


def fetch_lhb_today():
    """龙虎榜"""
    today_str = date.today().strftime('%Y%m%d')
    try:
        df = ak.stock_lhb_detail_em(start_date=today_str, end_date=today_str)
    except Exception as e:
        logger.warning(f"lhb skipped: {e}")
        return
    if df.empty:
        return
    rows = []
    for _, r in df.iterrows():
        code = str(r.get('代码','')).zfill(6)
        ts = code + ('.SH' if code.startswith('6') else '.SZ')
        rows.append((date.today(), ts, r.get('名称',''),
                     r.get('上榜原因',''), 0, 0, r.get('龙虎榜净买额',0), False))
    conn = get_db(); cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO lhb_detail (trade_date, ts_code, stock_name, reason, buy_amt, sell_amt, net_amt, is_inst)
        VALUES %s;
    """, rows)
    conn.commit()
    logger.info(f"lhb: {len(rows)} rows")
    cur.close(); conn.close()


def fetch_hsgt_top10():
    """北向资金 top10"""
    try:
        df = ak.stock_hsgt_hold_stock_em(market='北向', indicator='今日排行')
    except Exception as e:
        logger.warning(f"hsgt skipped: {e}")
        return
    rows = []
    for _, r in df.iterrows():
        code = str(r.get('代码','')).zfill(6)
        ts = code + ('.SH' if code.startswith('6') else '.SZ')
        rows.append((ts, date.today(), 0, r.get('今日持股市值',0), 0))
    if rows:
        conn = get_db(); cur = conn.cursor()
        execute_values(cur, """
            INSERT INTO hsgt_individual VALUES %s
            ON CONFLICT (ts_code, trade_date) DO UPDATE SET
            hold_market_cap=EXCLUDED.hold_market_cap;
        """, rows)
        conn.commit()
        logger.info(f"hsgt: {len(rows)} rows")
        cur.close(); conn.close()


if __name__ == '__main__':
    ensure_market_tables()
    fetch_daily_quotes_today()
    time.sleep(2)
    fetch_lhb_today()
    time.sleep(2)
    fetch_hsgt_top10()
