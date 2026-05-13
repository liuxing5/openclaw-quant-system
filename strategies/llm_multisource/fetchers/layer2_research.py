"""Layer 2: Research Reports -- 东财/THS profit forecasts"""
import re
import time
from loguru import logger


def fetch_em_profit_forecast(make_signal) -> list:
    """东财盈利预测 -- stock_profit_forecast_em(symbol='')

    Fetches full market consensus EPS forecasts (2600+ stocks).
    Returns make_signal() tuples with source='东财-盈利预测', tier=1.
    Content: EPS forecasts 2025-2028 + buy/hold/sell ratings.
    """
    import akshare as ak
    rows = []

    try:
        logger.debug("东财盈利预测: stock_profit_forecast_em(symbol='')")
        df = ak.stock_profit_forecast_em(symbol='')
        if df is None or not hasattr(df, 'empty') or df.empty:
            logger.warning("东财盈利预测返回空")
            return rows

        col_code = next((c for c in ['代码', '股票代码'] if c in df.columns), None)
        col_name = next((c for c in ['名称', '股票名称'] if c in df.columns), None)
        col_reports = next((c for c in ['研报数'] if c in df.columns), None)
        col_buy = next((c for c in df.columns if '买入' in c), None)
        col_hold = next((c for c in df.columns if '增持' in c), None)

        # EPS columns: 2025预测每股收益, 2026预测每股收益, etc.
        eps_cols = [c for c in df.columns if '预测每股收益' in c]

        if not col_code:
            logger.warning(f"东财盈利预测列不全: {list(df.columns)[:10]}")
            return rows

        for _, r in df.iterrows():
            try:
                raw_code = str(r.get(col_code, '') or '')
                code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
                if not code or code == 'nan' or len(code) < 6:
                    continue

                name = str(r.get(col_name, '') or '') if col_name else ''
                ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')

                report_count = int(r.get(col_reports, 0) or 0)
                buy_count = int(r.get(col_buy, 0) or 0) if col_buy else 0
                hold_count = int(r.get(col_hold, 0) or 0) if col_hold else 0

                eps_parts = []
                for ec in eps_cols:
                    val = r.get(ec)
                    if val is not None and str(val) != 'nan':
                        year = re.search(r'(\d{4})', ec)
                        yr = year.group(1) if year else ec
                        eps_parts.append(f"{yr}EPS:{float(val):.2f}")

                content = (f"代码 {code} {name} 研报数:{report_count} "
                          f"买入:{buy_count} 增持:{hold_count} "
                          f"{' '.join(eps_parts)}")

                rows.append(make_signal(
                    source='东财-盈利预测', tier=1,
                    title=f"盈利预测: {name} {ts} {report_count}家覆盖",
                    content=content,
                ))
            except Exception:
                continue

        logger.info(f"东财盈利预测: {len(rows)} 条")

    except Exception as e:
        logger.warning(f"东财盈利预测失败: {e}")

    return rows


def fetch_ths_profit_forecast(make_signal) -> list:
    """同花顺盈利预测 -- stock_profit_forecast_ths(symbol, indicator='预测年报每股收益')

    Iterates over top-30 stocks from daily_quotes by market cap.
    Returns make_signal() tuples with source='THS-盈利预测', tier=1.
    Rate-limited: 0.3s between requests.
    """
    import akshare as ak
    import psycopg2
    rows = []

    # Get top-30 stocks by market cap from daily_quotes
    try:
        from core.db.connection import get_db
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT ts_code FROM daily_quotes
            WHERE trade_date = (SELECT MAX(trade_date) FROM daily_quotes)
            ORDER BY amount DESC NULLS LAST
            LIMIT 30;
        """)
        codes = [row[0] for row in cur.fetchall()]
        cur.close(); conn.close()
    except Exception as e:
        logger.debug(f"THS预测: 无法获取top30股票列表: {e}")
        return rows

    if not codes:
        logger.info("THS预测: 无股票列表")
        return rows

    # Batch lookup stock names to avoid per-stock DB connections
    name_map = {}
    try:
        from core.db.connection import get_db as get_db2
        conn2 = get_db2()
        cur2 = conn2.cursor()
        cur2.execute(
            "SELECT ts_code, stock_name FROM stock_basic_info WHERE ts_code = ANY(%s)",
            (codes,)
        )
        for row in cur2.fetchall():
            name_map[row[0]] = row[1] or ''
        cur2.close(); conn2.close()
    except Exception:
        pass

    for ts_code in codes:
        try:
            code = ts_code.split('.')[0]
            df = ak.stock_profit_forecast_ths(symbol=code, indicator='预测年报每股收益')
            if df is None or not hasattr(df, 'empty') or df.empty:
                continue

            col_year = next((c for c in ['年度', '年份'] if c in df.columns), None)
            col_count = next((c for c in ['预测机构数', '机构数'] if c in df.columns), None)
            col_mean = next((c for c in ['均值', '平均值', '预测均值'] if c in df.columns), None)
            col_min = next((c for c in ['最小值', '最小'] if c in df.columns), None)
            col_max = next((c for c in ['最大值', '最大'] if c in df.columns), None)
            col_industry_avg = next((c for c in ['行业平均数', '行业均值'] if c in df.columns), None)

            if not col_year or not col_mean:
                continue

            # Only take the nearest year forecast
            first = df.iloc[0]
            year = str(first.get(col_year, ''))
            mean_eps = float(first.get(col_mean, 0) or 0)
            inst_count = int(first.get(col_count, 0) or 0) if col_count else 0
            min_eps = float(first.get(col_min, 0) or 0) if col_min else 0
            max_eps = float(first.get(col_max, 0) or 0) if col_max else 0
            industry_avg = float(first.get(col_industry_avg, 0) or 0) if col_industry_avg else 0

            stock_name = name_map.get(ts_code, '')

            content = (f"代码 {code} {stock_name} {year}年预测EPS: "
                      f"均值{mean_eps:.2f} 区间[{min_eps:.2f},{max_eps:.2f}] "
                      f"机构数:{inst_count} 行业均值:{industry_avg:.2f}")

            rows.append(make_signal(
                source='THS-盈利预测', tier=1,
                title=f"THS预测: {stock_name} {ts_code} {year}年EPS {mean_eps:.2f}",
                content=content,
            ))

            time.sleep(0.3)
        except Exception as e:
            logger.debug(f"THS预测 {ts_code} 失败: {e}")
            continue

    logger.info(f"THS盈利预测: {len(rows)} 条")
    return rows
