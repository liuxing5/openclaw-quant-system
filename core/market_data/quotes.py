"""行情数据每日采集 - AKShare 全市场为主，BaoStock/Tushare/yfinance 为备用"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
import re
import time
from datetime import datetime, date, timedelta, timezone
import pandas as pd
from psycopg2.extras import execute_values
from loguru import logger

from core.db.connection import get_db_fresh
from core.utils.env import load_project_env
from core.utils.ts_code import pure_to_ts_code
from core.utils.trading_calendar import is_trading_day as _calendar_is_trading_day

load_project_env()

# 北京时间时区
BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date():
    """获取北京时间日期（解决 GitHub Actions UTC 时区问题）"""
    return datetime.now(BEIJING_TZ).date()


def is_trading_day(d):
    """判断是否为交易日。委托给 core.utils.trading_calendar，含节假日识别。"""
    return _calendar_is_trading_day(d)


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
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        cur.execute(sql); conn.commit()
        cur.close()
    finally:
        if conn and not conn.closed:
            conn.close()


def fetch_with_akshare_full():
    """全 A 股一次性快照 - 新浪源优先，东财兜底"""
    import akshare as ak

    df = None

    logger.info("AKShare 全市场快照（新浪源）...")
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_spot()
            if df is not None and not df.empty:
                logger.info(f"新浪源成功: {len(df)} 条")
                break
        except Exception as e:
            if attempt < 2:
                wait = 2 * (attempt + 1)
                logger.warning(f"AKShare 新浪源失败 (尝试 {attempt+1}/3), 等待 {wait}s: {e}")
                time.sleep(wait)
            else:
                logger.warning(f"AKShare 新浪源最终失败，切换东财: {e}")
                break

    if df is None or df.empty:
        logger.info("AKShare 全市场快照（东财源）...")
        try:
            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty:
                logger.info(f"东财源成功: {len(df)} 条")
        except Exception as e:
            logger.error(f"AKShare 东财源失败: {e}")
            return None

    if df is None or df.empty:
        logger.warning("AKShare 所有源均返回空")
        return None

    is_em = '最新价' in df.columns
    code_col = '代码' if is_em else 'code'

    logger.info(f"解析 {len(df)} 条原始数据 (is_em={is_em}, code_col={code_col})")
    logger.debug(f"可用列: {list(df.columns)}")

    rows = []
    today = get_beijing_date()
    skipped_prefix = 0
    skipped_price = 0
    debug_count = 0
    for _, r in df.iterrows():
        raw_code = str(r.get(code_col, '') or '')
        # 清理代码：去掉 sh./sz. 前缀，只保留数字
        code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
        if debug_count < 5:
            logger.debug(f"代码解析: raw={raw_code!r} cleaned={code!r} valid={code.startswith(('6', '688', '000', '001', '002', '003', '300', '301'))}")
            debug_count += 1
        if not code or not (code.startswith(('6', '688', '000', '001', '002', '003', '300', '301'))):
            skipped_prefix += 1
            continue
        ts = pure_to_ts_code(code)

        if is_em:
            latest = r.get('最新价')
            if not latest or pd.isna(latest) or latest == 0:
                skipped_price += 1
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
                skipped_price += 1
                continue
            rows.append((
                ts, today,
                r.get('open'), r.get('high'), r.get('low'), latest,
                int(r.get('volume', 0)) if pd.notna(r.get('volume')) else None,
                r.get('amount'),
                r.get('changepercent'), r.get('turnoverratio'),
            ))

    logger.info(f"解析完成: {len(rows)} 条有效, 跳过前缀{skipped_prefix}, 跳过价格{skipped_price}")
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
        today_str = get_beijing_date().strftime('%Y-%m-%d')

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

    today_str = get_beijing_date().strftime('%Y%m%d')

    try:
        df = pro.daily(trade_date=today_str)
    except Exception as e:
        logger.warning(f"Tushare daily 获取失败: {e}")
        try:
            yesterday = get_beijing_date() - timedelta(days=1)
            df = pro.daily(trade_date=yesterday.strftime('%Y%m%d'))
        except Exception as e2:
            logger.warning(f"Tushare 备用日期也失败: {e2}")
            return None

    if df is None or df.empty:
        logger.warning("Tushare 返回空数据")
        return None

    logger.info(f"Tushare 获取到 {len(df)} 条行情数据")

    rows = []
    for _, r in df.iterrows():
        vol_raw = r.get('vol')
        amt_raw = r.get('amount')
        rows.append((
            r.get('ts_code'),
            pd.to_datetime(r.get('trade_date')).date(),
            r.get('open'), r.get('high'), r.get('low'), r.get('close'),
            int(vol_raw * 100) if pd.notna(vol_raw) and vol_raw else None,
            amt_raw * 1000 if pd.notna(amt_raw) and amt_raw else None,
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
    today = get_beijing_date()

    for code in major_stocks:
        try:
            ticker = yf.Ticker(code)
            hist = ticker.history(period='2d')
            if hist.empty:
                continue
            latest = hist.iloc[-1]
            prev = hist.iloc[-2] if len(hist) > 1 else latest

            pct_chg = ((latest['Close'] - prev['Close']) / prev['Close'] * 100) if prev['Close'] > 0 else 0.0

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
        try:
            rows = fetch_with_tushare()
        except ModuleNotFoundError:
            pass

    # 4. yfinance（最后）
    if not rows:
        try:
            rows = fetch_with_yfinance()
        except ModuleNotFoundError:
            pass

    if not rows:
        logger.error("所有数据源均失败")
        return

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        execute_values(cur, """
            INSERT INTO daily_quotes (ts_code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover_rate)
            VALUES %s
            ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                open=COALESCE(EXCLUDED.open, daily_quotes.open),
                high=COALESCE(EXCLUDED.high, daily_quotes.high),
                low=COALESCE(EXCLUDED.low, daily_quotes.low),
                close=COALESCE(EXCLUDED.close, daily_quotes.close),
                volume=COALESCE(EXCLUDED.volume, daily_quotes.volume),
                amount=COALESCE(EXCLUDED.amount, daily_quotes.amount),
                pct_chg=COALESCE(EXCLUDED.pct_chg, daily_quotes.pct_chg),
                turnover_rate=COALESCE(EXCLUDED.turnover_rate, daily_quotes.turnover_rate);
        """, rows)
        conn.commit()
        logger.info(f"daily_quotes 入库: {len(rows)} 条")
        cur.close()
    except Exception as e:
        logger.error(f"daily_quotes 入库失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()

    _supplement_with_tushare_daily_basic()


def _supplement_with_tushare_daily_basic():
    """用 Tushare daily_basic 补充 turnover_rate/volume_ratio/pe/pb 等字段"""
    token = os.getenv('TUSHARE_TOKEN')
    if not token:
        logger.debug("TUSHARE_TOKEN 未设置，跳过 daily_basic 补充")
        return

    try:
        import tushare as ts
    except ImportError:
        logger.debug("tushare 未安装，跳过 daily_basic 补充")
        return

    today = get_beijing_date()
    date_str = today.strftime('%Y%m%d')

    try:
        ts.set_token(token)
        pro = ts.pro_api()
        df = pro.daily_basic(trade_date=date_str,
                             fields='ts_code,trade_date,turnover_rate,volume_ratio,pe,pb')
    except Exception as e:
        logger.warning(f"Tushare daily_basic 补充失败: {e}")
        return

    if df is None or df.empty:
        logger.debug("Tushare daily_basic 返回空数据")
        return

    logger.info(f"Tushare daily_basic 返回 {len(df)} 条，补充 turnover_rate/volume_ratio/PE/PB")

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        updated = 0
        for _, r in df.iterrows():
            ts_code = r.get('ts_code')
            tr = r.get('turnover_rate')
            vr = r.get('volume_ratio')
            pe = r.get('pe')
            pb = r.get('pb')
            if not ts_code:
                continue
            try:
                cur.execute("""
                    UPDATE daily_quotes SET
                        turnover_rate = COALESCE(%s, turnover_rate),
                        volume_ratio = COALESCE(%s, volume_ratio),
                        pe_ratio = COALESCE(%s, pe_ratio),
                        pb_ratio = COALESCE(%s, pb_ratio)
                    WHERE ts_code = %s AND trade_date = %s
                      AND (turnover_rate IS NULL OR volume_ratio IS NULL
                           OR pe_ratio IS NULL OR pb_ratio IS NULL);
                """, (tr, vr, pe, pb, ts_code, today))
                if cur.rowcount > 0:
                    updated += cur.rowcount
            except Exception:
                pass
        conn.commit()
        cur.close()
        logger.info(f"Tushare daily_basic 补充: {updated} 条更新")
    except Exception as e:
        logger.warning(f"Tushare daily_basic 入库失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()


def _compute_volume_ratio_sql():
    """用SQL计算回填 volume_ratio = 今日成交量 / 过去5日平均成交量"""
    today = get_beijing_date()
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        cur.execute("""
            UPDATE daily_quotes dq
            SET volume_ratio = CASE
                WHEN avg_vol > 0 THEN ROUND(dq.volume::numeric / avg_vol, 2)
                ELSE NULL
            END
            FROM (
                SELECT ts_code, AVG(volume) as avg_vol
                FROM daily_quotes
                WHERE trade_date < %s
                  AND trade_date >= %s - INTERVAL '10 days'
                  AND volume IS NOT NULL AND volume > 0
                GROUP BY ts_code
                HAVING COUNT(*) >= 3
            ) sub
            WHERE dq.ts_code = sub.ts_code
              AND dq.trade_date = %s
              AND dq.volume IS NOT NULL AND dq.volume > 0
              AND dq.volume_ratio IS NULL;
        """, (today, today, today))
        updated = cur.rowcount
        conn.commit()
        cur.close()
        if updated > 0:
            logger.info(f"SQL计算 volume_ratio: {updated} 条")
    except Exception as e:
        logger.warning(f"SQL计算 volume_ratio 失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()


def _compute_amplitude_sql():
    """用SQL计算回填 amplitude = (最高-最低)/昨收 * 100"""
    today = get_beijing_date()
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        cur.execute("""
            UPDATE daily_quotes dq
            SET amplitude = CASE
                WHEN prev_close > 0 THEN ROUND((dq.high - dq.low) / prev_close * 100, 2)
                ELSE NULL
            END
            FROM (
                SELECT ts_code, close as prev_close
                FROM daily_quotes
                WHERE trade_date = (
                    SELECT MAX(trade_date) FROM daily_quotes
                    WHERE trade_date < %s
                )
            ) sub
            WHERE dq.ts_code = sub.ts_code
              AND dq.trade_date = %s
              AND dq.high IS NOT NULL AND dq.low IS NOT NULL
              AND dq.amplitude IS NULL;
        """, (today, today))
        updated = cur.rowcount
        conn.commit()
        cur.close()
        if updated > 0:
            logger.info(f"SQL计算 amplitude: {updated} 条")
    except Exception as e:
        logger.warning(f"SQL计算 amplitude 失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()


def fetch_lhb_today():
    """龙虎榜"""
    today_str = get_beijing_date().strftime('%Y%m%d')
    try:
        import akshare as ak
        df = ak.stock_lhb_detail_em(start_date=today_str, end_date=today_str)
    except Exception as e:
        try:
            err_msg = str(e)
        except Exception:
            err_msg = repr(e)
        logger.warning(f"lhb skipped: {err_msg}")
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
            ts = pure_to_ts_code(code)
            name = r.get(col_name, '') or '' if col_name else ''
            reason = r.get(col_reason, '') or '' if col_reason else ''
            net = r.get(col_net, 0) or 0 if col_net else 0
            rows.append((get_beijing_date(), ts, name, reason, 0, 0, net, False))
        except Exception as e:
            logger.debug(f"lhb row error: {e}")
            continue
    if not rows:
        return
    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor()
        execute_values(cur, """
            INSERT INTO lhb_detail (trade_date, ts_code, stock_name, reason, buy_amt, sell_amt, net_amt, is_inst)
            VALUES %s
            ON CONFLICT ON CONSTRAINT lhb_unique DO NOTHING;
        """, rows)
        conn.commit()
        logger.info(f"lhb: {len(rows)} rows")
        cur.close()
    except Exception as e:
        logger.error(f"lhb 入库失败: {e}")
    finally:
        if conn and not conn.closed:
            conn.close()


def fetch_hsgt_top10():
    """北向资金 top10"""
    try:
        import akshare as ak
        df = ak.stock_hsgt_hold_stock_em(market='北向', indicator='今日排行')
    except Exception as e:
        logger.warning(f"hsgt skipped: {e}")
        return
    if df is None or (hasattr(df, 'empty') and df.empty):
        logger.info("hsgt: 无数据")
        return
    col_code = next((c for c in ['代码', '股票代码', 'code'] if c in df.columns), None)
    col_mktcap = next((c for c in ['今日持股市值', '持股市值', '持有市值', 'hold_market_cap'] if c in df.columns), None)
    col_shares = next((c for c in ['今日持股数量', '持股数量', '持股', 'hold_shares'] if c in df.columns), None)
    col_net = next((c for c in ['今日增仓', '增仓', '净买入', 'net_buy'] if c in df.columns), None)
    rows = []
    for _, r in df.iterrows():
        code = str(r.get(col_code, '') or '').zfill(6) if col_code else ''
        if not code or code == '000000':
            continue
        ts = pure_to_ts_code(code)
        mktcap = float(r.get(col_mktcap, 0) or 0) if col_mktcap else 0
        shares = int(float(r.get(col_shares, 0) or 0)) if col_shares else 0
        net = float(r.get(col_net, 0) or 0) if col_net else 0
        rows.append((ts, get_beijing_date(), shares, mktcap, net))
    if rows:
        conn = None
        try:
            conn = get_db_fresh()
            cur = conn.cursor()
            execute_values(cur, """
                INSERT INTO hsgt_individual (ts_code, trade_date, hold_shares, hold_market_cap, net_buy_amount)
                VALUES %s
                ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                hold_shares=COALESCE(EXCLUDED.hold_shares, hsgt_individual.hold_shares),
                hold_market_cap=COALESCE(EXCLUDED.hold_market_cap, hsgt_individual.hold_market_cap),
                net_buy_amount=COALESCE(EXCLUDED.net_buy_amount, hsgt_individual.net_buy_amount);
            """, rows)
            conn.commit()
            logger.info(f"hsgt: {len(rows)} rows")
            cur.close()
        except Exception as e:
            logger.error(f"hsgt 入库失败: {e}")
        finally:
            if conn and not conn.closed:
                conn.close()


if __name__ == '__main__':
    today = get_beijing_date()
    
    # 非交易日跳过行情采集
    if not is_trading_day(today):
        logger.warning(f"{today} 非交易日，跳过行情采集")
        exit(0)
    
    import concurrent.futures
    ensure_market_tables()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f1 = executor.submit(fetch_daily_quotes_today)
        f2 = executor.submit(fetch_lhb_today)
        f3 = executor.submit(fetch_hsgt_top10)
        f1.result()
        f2.result()
        f3.result()

    _compute_volume_ratio_sql()
    _compute_amplitude_sql()
