"""Layer 4: Fundamentals -- mootdx financial data"""
import time
from loguru import logger

from . import FetchResult


def fetch_mootdx_fundamentals(make_signal, codes=None) -> list:
    rows = FetchResult()

    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market='std', timeout=5)
        if client is None:
            logger.debug("MootDX财务: 连接失败")
            return rows
    except ImportError:
        logger.debug("mootdx 未安装，跳过财务数据")
        return rows
    except Exception as e:
        logger.debug(f"MootDX财务连接失败: {e}")
        return rows

    if codes is None:
        conn = None
        try:
            from core.db.connection import get_db_fresh
            conn = get_db_fresh()
            cur = conn.cursor()
            cur.execute("""
                SELECT ts_code FROM daily_quotes
                WHERE trade_date = (SELECT MAX(trade_date) FROM daily_quotes)
                ORDER BY amount DESC NULLS LAST
                LIMIT 200;
            """)
            codes = [row[0] for row in cur.fetchall()]
            cur.close()
        except Exception:
            return rows
        finally:
            if conn and not conn.closed:
                conn.close()

    if not codes:
        return rows

    fundamentals = []

    for ts in codes[:200]:
        try:
            parts = ts.split('.')
            if len(parts) != 2:
                continue
            code, market = parts
            mkt = 1 if market == 'SH' else 0

            try:
                fin = client.finance(symbol=code)
                if fin is not None and hasattr(fin, 'empty') and not fin.empty:
                    for _, r in fin.iterrows():
                        try:
                            fundamentals.append({
                                'ts_code': ts,
                                'report_date': r.get('report_date', None),
                                'revenue': r.get('revenue', None),
                                'net_profit': r.get('net_profit', None),
                                'eps': r.get('earnings_per_share', None),
                                'bps': r.get('book_value_per_share', None),
                                'roe': r.get('roe', None),
                            })
                        except Exception:
                            continue
            except Exception as e:
                logger.debug(f"MootDX finance {code}: {e}")

            time.sleep(0.2)

        except Exception:
            continue

    if not fundamentals and codes:
        try:
            import akshare as ak
            from datetime import datetime, timezone, timedelta
            beijing_tz = timezone(timedelta(hours=8))
            today = datetime.now(beijing_tz).date()
            for ts in codes[:50]:
                try:
                    code = ts.split('.')[0]
                    df = ak.stock_financial_abstract_ths(symbol=code, indicator='按报告期')
                    if df is not None and not df.empty:
                        latest = df.iloc[-1] if len(df) > 0 else None
                        if latest is not None:
                            fundamentals.append({
                                'ts_code': ts,
                                'report_date': str(latest.get('报告期', today)),
                                'revenue': float(latest.get('营业总收入', 0) or 0),
                                'net_profit': float(latest.get('净利润', 0) or 0),
                                'eps': float(latest.get('每股收益', 0) or 0),
                                'bps': float(latest.get('每股净资产', 0) or 0),
                            })
                    time.sleep(0.3)
                except Exception:
                    continue
            logger.info(f"AKShare财务数据(回退): {len(fundamentals)} 条记录")
        except ImportError:
            logger.debug("akshare 未安装，无法回退财务数据")
        except Exception as e:
            logger.debug(f"AKShare财务数据回退失败: {e}")

    if not fundamentals and codes:
        import os
        token = os.getenv('TUSHARE_TOKEN')
        if token:
            try:
                import tushare as ts
                ts.set_token(token)
                pro = ts.pro_api()
                for ts_code in codes[:200]:
                    try:
                        df = pro.fina_indicator(
                            ts_code=ts_code,
                            fields='ts_code,ann_date,end_date,roe,netprofit_margin,grossprofit_margin,debt_to_assets,op_yoy,or_yoy'
                        )
                        if df is None or df.empty:
                            continue
                        latest = df.iloc[0]
                        end_date = str(latest.get('end_date', ''))
                        if not end_date or len(end_date) < 8:
                            continue
                        report_date = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
                        fundamentals.append({
                            'ts_code': ts_code,
                            'report_date': report_date,
                            'net_margin': latest.get('netprofit_margin'),
                            'gross_margin': latest.get('grossprofit_margin'),
                            'debt_ratio': latest.get('debt_to_assets'),
                            'revenue_yoy': latest.get('or_yoy'),
                            'profit_yoy': latest.get('op_yoy'),
                        })
                        time.sleep(0.15)
                    except Exception:
                        continue
                logger.info(f"Tushare财务数据(回退): {len(fundamentals)} 条记录")
            except ImportError:
                logger.debug("tushare 未安装，跳过财务数据回退")
            except Exception as e:
                logger.debug(f"Tushare财务数据回退失败: {e}")

    rows._fundamentals = fundamentals

    logger.info(f"MootDX财务数据: {len(fundamentals)} 条记录 from {len(codes[:200])} 只股票")
    return rows
