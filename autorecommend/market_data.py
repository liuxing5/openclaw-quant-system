"""行情数据每日采集 - AKShare 全市场为主，BaoStock/Tushare/yfinance 为备用"""
import os
import time
from datetime import datetime, date
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
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
        net_amt NUMERIC(20,2), is_inst BOOLEAN,
        CONSTRAINT lhb_unique UNIQUE (trade_date, ts_code, reason)
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


def fetch_with_akshare_full():
    """全 A 股一次性快照 - 新浪源优先，东财兜底"""
    import akshare as ak

    logger.info("AKShare 全市场快照（新浪源）...")
    df = None
    for attempt in range(2):
        try:
            df = ak.stock_zh_a_spot()
            if df is not None and not df.empty:
                break
        except Exception as e:
            if attempt < 1:
                logger.warning(f"AKShare 新浪源失败 (尝试 {attempt+1}/2): {e}")
                time.sleep(2)
            else:
                logger.warning(f"AKShare 新浪源最终失败，切换东财: {e}")
                break

    if df is None or df.empty:
        logger.info("AKShare 全市场快照（东财源）...")
        for attempt in range(2):
            try:
                df = ak.stock_zh_a_spot_em()
                if df is not None and not df.empty:
                    break
            except Exception as e:
                if attempt < 1:
                    logger.warning(f"AKShare 东财源失败 (尝试 {attempt+1}/2): {e}")
                    time.sleep(3)
                else:
                    logger.error(f"AKShare 东财源最终失败: {e}")
                    return None

    if df is None or df.empty:
        logger.warning("AKShare 返回空")
        return None

    is_em = '最新价' in df.columns

    rows = []
    today = date.today()
    for _, r in df.iterrows():
        code_col = '代码' if is_em else 'code'
        code = str(r.get(code_col, '')).zfill(6)
        if not code or not (code.startswith(('6', '688', '000', '001', '002', '003', '300', '301'))):
            continue
        ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')

        if is_em:
            latest = r.get('最新价')
            if not latest or pd.isna(latest) or latest == 0:
                continue
            rows.append((
                ts, today,
                r.get('今开'), r.get('最高'), r.get('最低'), latest,
                int(r['成交量']) if pd.notna(r.get('成交量')) else None,
                r.get('成交额'),
                r.get('涨跌幅'), r.get('换手率'),
            ))
        else:
            latest = r.get('trade')
            if not latest or pd.isna(latest) or latest == 0:
                continue
            rows.append((
                ts, today,
                r.get('open'), r.get('high'), r.get('low'), latest,
                int(r.get('volume', 0)) if pd.notna(r.get('volume')) else None,
                r.get('amount'),
                r.get('pricechange'), r.get('turnoverratio'),
            ))

    return rows


def fetch_with_baostock():
    """用 BaoStock 获取 A 股核心股票行情（兜底）"""
    import baostock as bs

    logger.info("使用 BaoStock 获取 A 股行情数据（兜底）...")

    lg = bs.login()
    if lg.error_code != '0':
        logger.warning(f"BaoStock 登录失败: {lg.error_msg}")
        return None

    try:
        today_str = date.today().strftime('%Y-%m-%d')

        major_codes = [
            'sh.600519', 'sh.601318', 'sh.600036', 'sh.601166', 'sh.600887',
            'sh.601398', 'sh.601288', 'sh.601939', 'sh.601988', 'sh.601628',
            'sh.600030', 'sh.601688', 'sh.601888', 'sh.601012', 'sh.603259',
            'sh.600276', 'sh.603288', 'sh.600900', 'sh.601899', 'sh.601088',
            'sz.000858', 'sz.000333', 'sz.002594', 'sz.000651', 'sz.002415',
            'sz.000725', 'sz.002714', 'sz.000001', 'sz.000002', 'sz.002475',
            'sz.300750', 'sz.300059', 'sz.000568', 'sz.002304', 'sz.002352',
        ]

        rows = []
        for code in major_codes:
            try:
                rs = bs.query_history_k_data_plus(
                    code,
                    "date,code,open,high,low,close,volume,amount,turn",
                    start_date=today_str, end_date=today_str,
                    frequency="d", adjustflag="3"
                )

                if rs.error_code != '0' or not rs.next():
                    continue

                row_data = rs.get_row_data()
                if not row_data or len(row_data) < 9:
                    continue

                ts_code = code[3:] + ('.SH' if code.startswith('sh.') else '.SZ')

                rows.append((
                    ts_code,
                    pd.to_datetime(row_data[0]).date(),
                    float(row_data[2]) if row_data[2] else None,
                    float(row_data[3]) if row_data[3] else None,
                    float(row_data[4]) if row_data[4] else None,
                    float(row_data[5]) if row_data[5] else None,
                    int(float(row_data[6])) if row_data[6] else None,
                    float(row_data[7]) if row_data[7] else None,
                    None,
                    float(row_data[8]) if row_data[8] else None,
                ))
            except Exception as e:
                logger.debug(f"BaoStock {code} 失败: {e}")
                continue

        logger.info(f"BaoStock 获取到 {len(rows)} 条行情数据")
        return rows if rows else None

    except Exception as e:
        logger.warning(f"BaoStock 获取失败: {e}")
        return None
    finally:
        bs.logout()


def fetch_with_tushare():
    """用 Tushare 获取 A 股全市场行情（备用）"""
    import tushare as ts

    token = os.getenv('TUSHARE_TOKEN')
    if not token:
        logger.warning("TUSHARE_TOKEN 未设置，跳过 Tushare")
        return None

    logger.info("使用 Tushare 获取 A 股行情数据...")
    ts.set_token(token)
    pro = ts.pro_api()

    today_str = date.today().strftime('%Y%m%d')

    try:
        df = pro.daily(trade_date=today_str)
    except Exception as e:
        logger.warning(f"Tushare daily 获取失败: {e}")
        try:
            df = pro.daily(trade_date=(date.today().replace(day=max(1, date.today().day-1))).strftime('%Y%m%d'))
        except Exception as e2:
            logger.warning(f"Tushare 备用日期也失败: {e2}")
            return None

    if df is None or df.empty:
        logger.warning("Tushare 返回空数据")
        return None

    logger.info(f"Tushare 获取到 {len(df)} 条行情数据")

    rows = []
    for _, r in df.iterrows():
        rows.append((
            r.get('ts_code'),
            pd.to_datetime(r.get('trade_date')).date(),
            r.get('open'), r.get('high'), r.get('low'), r.get('close'),
            r.get('vol') * 100 if r.get('vol') else None,
            r.get('amount') * 1000 if r.get('amount') else None,
            r.get('pct_chg'), None,
        ))

    return rows


def fetch_with_yfinance():
    """用 yfinance 获取 A 股行情（最后兜底）"""
    import yfinance as yf

    logger.info("使用 yfinance 获取 A 股行情数据...")

    major_stocks = [
        '600519.SS', '601318.SS', '600036.SS', '601166.SS', '600887.SS',
        '601398.SS', '601288.SS', '601939.SS', '601988.SS', '601628.SS',
        '000858.SZ', '000333.SZ', '002594.SZ', '000651.SZ', '002415.SZ',
        '000725.SZ', '002714.SZ', '000001.SZ', '000002.SZ', '002475.SZ',
        '300750.SZ', '300059.SZ', '000568.SZ', '002304.SZ', '002352.SZ',
    ]

    rows = []
    today = date.today()

    for code in major_stocks:
        try:
            ticker = yf.Ticker(code)
            hist = ticker.history(period='2d')
            if hist.empty:
                continue
            latest = hist.iloc[-1]
            prev = hist.iloc[-2] if len(hist) > 1 else latest

            pct_chg = ((latest['Close'] - prev['Close']) / prev['Close'] * 100) if prev['Close'] != 0 else 0

            rows.append((
                code.replace('.SS', '.SH').replace('.SZ', '.SZ'),
                today,
                float(latest['Open']), float(latest['High']),
                float(latest['Low']), float(latest['Close']),
                int(latest['Volume']), float(latest['Volume'] * latest['Close']),
                float(pct_chg), None,
            ))
        except Exception as e:
            logger.debug(f"yfinance {code} 失败: {e}")
            continue

    logger.info(f"yfinance 获取到 {len(rows)} 条行情数据")
    return rows


def fetch_daily_quotes_today():
    """全市场当日行情快照 - AKShare 优先"""
    rows = None

    # 1. AKShare 全市场（首选，5300+ 只）
    rows = fetch_with_akshare_full()

    # 2. BaoStock 写死列表（兜底）
    if not rows:
        rows = fetch_with_baostock()

    # 3. Tushare（兜底）
    if not rows:
        rows = fetch_with_tushare()

    # 4. yfinance（最后）
    if not rows:
        rows = fetch_with_yfinance()

    if not rows:
        logger.error("所有数据源均失败")
        return

    conn = get_db(); cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO daily_quotes VALUES %s
        ON CONFLICT (ts_code, trade_date) DO UPDATE SET
            open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
            close=EXCLUDED.close, volume=EXCLUDED.volume,
            amount=EXCLUDED.amount, pct_chg=EXCLUDED.pct_chg,
            turnover_rate=EXCLUDED.turnover_rate;
    """, rows)
    conn.commit()
    logger.info(f"daily_quotes 入库: {len(rows)} 条")
    cur.close(); conn.close()


def fetch_lhb_today():
    """龙虎榜"""
    today_str = date.today().strftime('%Y%m%d')
    try:
        import akshare as ak
        df = ak.stock_lhb_detail_em(start_date=today_str, end_date=today_str)
    except Exception as e:
        logger.warning(f"lhb skipped: {e}")
        return
    if df is None:
        logger.warning("lhb: 接口返回 None")
        return
    if not hasattr(df, 'empty') or df.empty:
        logger.info("lhb: 无数据")
        return
    col_code = next((c for c in ['代码', '股票代码', 'code'] if c in df.columns), None)
    col_name = next((c for c in ['名称', '股票名称', 'name'] if c in df.columns), None)
    col_reason = next((c for c in ['上榜原因', '解读', 'reason'] if c in df.columns), None)
    col_net = next((c for c in ['龙虎榜净买额', '净买额', '净额'] if c in df.columns), None)
    if not col_code:
        logger.warning(f"lhb: 找不到代码列，可用列: {list(df.columns)}")
        return
    rows = []
    for _, r in df.iterrows():
        try:
            code = str(r.get(col_code, '') or '').zfill(6)
            if not code or code == 'nan' or len(code) < 4:
                continue
            ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
            name = r.get(col_name, '') or '' if col_name else ''
            reason = r.get(col_reason, '') or '' if col_reason else ''
            net = r.get(col_net, 0) or 0 if col_net else 0
            rows.append((date.today(), ts, name, reason, 0, 0, net, False))
        except Exception as e:
            logger.debug(f"lhb row error: {e}")
            continue
    if not rows:
        return
    conn = get_db(); cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO lhb_detail (trade_date, ts_code, stock_name, reason, buy_amt, sell_amt, net_amt, is_inst)
        VALUES %s
        ON CONFLICT (trade_date, ts_code, reason) DO NOTHING;
    """, rows)
    conn.commit()
    logger.info(f"lhb: {len(rows)} rows")
    cur.close(); conn.close()


def fetch_hsgt_top10():
    """北向资金 top10"""
    try:
        import akshare as ak
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
    import concurrent.futures
    ensure_market_tables()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f1 = executor.submit(fetch_daily_quotes_today)
        f2 = executor.submit(fetch_lhb_today)
        f3 = executor.submit(fetch_hsgt_top10)
        f1.result()
        f2.result()
        f3.result()
