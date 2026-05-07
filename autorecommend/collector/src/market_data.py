"""行情数据每日采集 - BaoStock 为主，Tushare/yfinance/AKShare 为备用"""
import os
import time
from datetime import datetime, date
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(BASE_DIR, '.env'))


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


def fetch_with_baostock():
    """用 BaoStock 获取 A 股核心股票行情（GitHub Actions 可用，无需 token）"""
    import baostock as bs
    
    logger.info("使用 BaoStock 获取 A 股行情数据...")
    
    lg = bs.login()
    if lg.error_code != '0':
        logger.warning(f"BaoStock 登录失败: {lg.error_msg}")
        return None
    
    try:
        today_str = date.today().strftime('%Y-%m-%d')
        
        # 核心股票列表（沪深300前100，确保 GitHub Actions 不超时）
        major_codes = [
            'sh.600519', 'sh.601318', 'sh.600036', 'sh.601166', 'sh.600887',
            'sh.601398', 'sh.601288', 'sh.601939', 'sh.601988', 'sh.601628',
            'sh.600030', 'sh.601688', 'sh.601888', 'sh.601012', 'sh.603259',
            'sh.600276', 'sh.603288', 'sh.600900', 'sh.601899', 'sh.601088',
            'sh.600111', 'sh.600160', 'sh.600176', 'sh.600315', 'sh.600801',
            'sh.601100', 'sh.603059', 'sh.603193', 'sh.603197', 'sh.603256',
            'sh.603379', 'sh.603605', 'sh.603737', 'sh.688017',
            'sh.600000', 'sh.600016', 'sh.600028', 'sh.600031', 'sh.600050',
            'sh.600104', 'sh.600115', 'sh.600150', 'sh.600196', 'sh.600219',
            'sh.600256', 'sh.600271', 'sh.600309', 'sh.600346', 'sh.600406',
            'sh.600436', 'sh.600438', 'sh.600460', 'sh.600515', 'sh.600547',
            'sh.600570', 'sh.600584', 'sh.600585', 'sh.600588', 'sh.600600',
            'sh.600660', 'sh.600690', 'sh.600703', 'sh.600745', 'sh.600760',
            'sh.600803', 'sh.600809', 'sh.600837', 'sh.600845', 'sh.600886',
            'sh.600905', 'sh.600919', 'sh.600926', 'sh.600958', 'sh.601006',
            'sh.601009', 'sh.601021', 'sh.601059', 'sh.601066', 'sh.601077',
            'sh.601099', 'sh.601111', 'sh.601117', 'sh.601138', 'sh.601169',
            'sh.601186', 'sh.601211', 'sh.601225', 'sh.601229', 'sh.601236',
            'sh.601238', 'sh.601319', 'sh.601328', 'sh.601336', 'sh.601360',
            'sh.601377', 'sh.601390', 'sh.601555', 'sh.601577', 'sh.601600',
            'sh.601601', 'sh.601607', 'sh.601618', 'sh.601633', 'sh.601658',
            'sz.000858', 'sz.000333', 'sz.002594', 'sz.000651', 'sz.002415',
            'sz.000725', 'sz.002714', 'sz.000001', 'sz.000002', 'sz.002475',
            'sz.300750', 'sz.300059', 'sz.000568', 'sz.002304', 'sz.002352',
            'sz.002049', 'sz.002230', 'sz.002271', 'sz.002460', 'sz.002466',
            'sz.000786', 'sz.002080', 'sz.002299', 'sz.002311', 'sz.002458',
        ]
        
        rows = []
        for i, code in enumerate(major_codes):
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
            
            if (i + 1) % 25 == 0:
                logger.info(f"BaoStock 进度: {i+1}/{len(major_codes)}")
        
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
    """用 yfinance 获取 A 股行情（备用）"""
    import yfinance as yf
    
    logger.info("使用 yfinance 获取 A 股行情数据...")
    
    major_stocks = [
        '600519.SS', '601318.SS', '600036.SS', '601166.SS', '600887.SS',
        '601398.SS', '601288.SS', '601939.SS', '601988.SS', '601628.SS',
        '600030.SS', '601688.SS', '601888.SS', '601012.SS', '603259.SS',
        '600276.SS', '603288.SS', '600900.SS', '601899.SS', '601088.SS',
        '000858.SZ', '000333.SZ', '002594.SZ', '000651.SZ', '002415.SZ',
        '000725.SZ', '002714.SZ', '000001.SZ', '000002.SZ', '002475.SZ',
        '300750.SZ', '300059.SZ', '000568.SZ', '002304.SZ', '002352.SZ',
        '002049.SZ', '002230.SZ', '002271.SZ', '002460.SZ', '002466.SZ',
        '000786.SZ', '002080.SZ', '002299.SZ', '002311.SZ', '002458.SZ',
        '002891.SZ', '002982.SZ', '300124.SZ', '300498.SZ', '301498.SZ',
        '600111.SS', '600160.SS', '600176.SS', '600315.SS', '600801.SS',
        '601100.SS', '603059.SS', '603193.SS', '603197.SS', '603256.SS',
        '603379.SS', '603605.SS', '603737.SS', '688017.SS',
        '600000.SS', '600016.SS', '600028.SS', '600031.SS', '600050.SS',
        '600104.SS', '600115.SS', '600150.SS', '600196.SS', '600219.SS',
        '600256.SS', '600271.SS', '600309.SS', '600346.SS', '600406.SS',
        '600436.SS', '600438.SS', '600460.SS', '600515.SS', '600547.SS',
        '600570.SS', '600584.SS', '600585.SS', '600588.SS', '600600.SS',
        '600660.SS', '600690.SS', '600703.SS', '600745.SS', '600760.SS',
        '600803.SS', '600809.SS', '600837.SS', '600845.SS', '600886.SS',
        '600905.SS', '600919.SS', '600926.SS', '600958.SS', '601006.SS',
        '601009.SS', '601021.SS', '601059.SS', '601066.SS', '601077.SS',
        '601099.SS', '601111.SS', '601117.SS', '601138.SS', '601169.SS',
        '601186.SS', '601211.SS', '601225.SS', '601229.SS', '601236.SS',
        '601238.SS', '601319.SS', '601328.SS', '601336.SS', '601360.SS',
        '601377.SS', '601390.SS', '601555.SS', '601577.SS', '601600.SS',
        '601601.SS', '601607.SS', '601618.SS', '601633.SS', '601658.SS',
        '601668.SS', '601669.SS', '601689.SS', '601698.SS', '601699.SS',
        '601728.SS', '601766.SS', '601788.SS', '601800.SS', '601808.SS',
        '601816.SS', '601818.SS', '601838.SS', '601857.SS', '601865.SS',
        '601866.SS', '601868.SS', '601872.SS', '601877.SS', '601878.SS',
        '601881.SS', '601898.SS', '601901.SS', '601916.SS', '601919.SS',
        '601985.SS', '601989.SS', '601995.SS', '601997.SS', '601998.SS',
        '603000.SS', '603019.SS', '603160.SS', '603501.SS', '603799.SS',
        '603986.SS', '603993.SS',
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


def fetch_with_akshare():
    """用 AKShare 获取行情（本地可用，GitHub Actions 不可用）"""
    import akshare as ak
    
    logger.info("使用 AKShare 获取 A 股行情数据...")
    df = ak.stock_zh_a_spot_em()
    
    rows = []
    today = date.today()
    for _, r in df.iterrows():
        code = str(r['代码']).zfill(6)
        ts = code + ('.SH' if code.startswith('6') else '.SZ')
        rows.append((
            ts, today,
            r.get('今开'), r.get('最高'), r.get('最低'), r.get('最新价'),
            r.get('成交量'), r.get('成交额'),
            r.get('涨跌幅'), r.get('换手率'),
        ))
    
    logger.info(f"AKShare 获取到 {len(rows)} 条行情数据")
    return rows


def fetch_daily_quotes_today():
    """全市场当日行情快照 - 多数据源降级"""
    rows = None
    
    # 1. BaoStock（优先，无需 token）
    if rows is None:
        rows = fetch_with_baostock()
    
    # 2. Tushare（备用）
    if rows is None:
        rows = fetch_with_tushare()
    
    # 3. yfinance（备用）
    if rows is None:
        rows = fetch_with_yfinance()
    
    # 4. AKShare（最后）
    if rows is None:
        try:
            rows = fetch_with_akshare()
        except Exception as e:
            logger.warning(f"AKShare 也失败: {e}")
    
    if rows is None or len(rows) == 0:
        logger.error("所有数据源均失败，无法获取行情数据")
        return
    
    conn = get_db(); cur = conn.cursor()
    execute_values(cur, """
        INSERT INTO daily_quotes VALUES %s
        ON CONFLICT (ts_code, trade_date) DO UPDATE SET
        close=EXCLUDED.close, pct_chg=EXCLUDED.pct_chg,
        volume=EXCLUDED.volume, amount=EXCLUDED.amount;
    """, rows)
    conn.commit()
    logger.info(f"daily_quotes: {len(rows)} rows saved")
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
    ensure_market_tables()
    fetch_daily_quotes_today()
    time.sleep(2)
    fetch_lhb_today()
    time.sleep(2)
    fetch_hsgt_top10()
