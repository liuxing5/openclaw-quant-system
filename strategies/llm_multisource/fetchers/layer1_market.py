"""Layer 1: Market Data -- Tencent qt.gtimg, THS strong stocks/concepts, mootdx"""
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from loguru import logger

BEIJING_TZ = timezone(timedelta(hours=8))

# ============================================================
#  同花顺 THS 强势股排名 (存储到 strong_stock_rank 表)
# ============================================================

def fetch_ths_strong_stocks_structured(make_signal) -> list:
    """同花顺强势股 -- 同时返回信号和结构化数据

    Returns make_signal() tuples + attaches _strong_stock_rank data.
    """
    import akshare as ak
    rows = []
    strong_data = []
    today = datetime.now(BEIJING_TZ).date()

    fetchers = [
        ('lxsz', lambda: ak.stock_rank_lxsz_ths()),
        ('cxg', lambda: ak.stock_rank_cxg_ths(symbol='创月新高')),
        ('ljqd', lambda: ak.stock_rank_ljqd_ths()),
    ]

    for rank_type, fetcher in fetchers:
        try:
            logger.debug(f"THS强势股结构化: {rank_type}")
            df = fetcher()
            if df is None or not hasattr(df, 'empty') or df.empty:
                continue

            col_code = next((c for c in ['代码', '股票代码', 'code'] if c in df.columns), None)
            col_name = next((c for c in ['名称', '股票名称', 'name'] if c in df.columns), None)
            col_days = next((c for c in ['连续上涨天数', '天数', 'days'] if c in df.columns), None)
            col_chg = next((c for c in ['涨跌幅', '累计涨跌幅', 'pct_chg'] if c in df.columns), None)
            col_turnover = next((c for c in ['换手率', 'turnover'] if c in df.columns), None)
            col_industry = next((c for c in ['行业', 'industry'] if c in df.columns), None)
            col_price = next((c for c in ['最新价', 'price', 'close'] if c in df.columns), None)

            if not col_code:
                continue

            for pos, (_, r) in enumerate(df.head(50).iterrows(), 1):
                try:
                    raw_code = str(r.get(col_code, '') or '')
                    code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
                    if not code or len(code) < 6:
                        continue

                    name = str(r.get(col_name, '') or '') if col_name else ''
                    ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                    days = int(r.get(col_days, 0) or 0) if col_days else 0
                    chg = float(r.get(col_chg, 0) or 0) if col_chg else 0
                    turnover = float(r.get(col_turnover, 0) or 0) if col_turnover else 0
                    industry = str(r.get(col_industry, '') or '') if col_industry else ''
                    price = float(r.get(col_price, 0) or 0) if col_price else 0

                    strong_data.append({
                        'trade_date': today,
                        'ts_code': ts,
                        'stock_name': name,
                        'rank_type': rank_type,
                        'rank_position': pos,
                        'consecutive_days': days,
                        'stage_chg_pct': chg,
                        'cumulative_turnover': turnover,
                        'industry': industry,
                        'latest_price': price,
                    })

                    if pos <= 10:
                        rows.append(make_signal(
                            source='THS-强势股', tier=1,
                            title=f"强势: {name} {ts} {rank_type} 第{pos}名",
                            content=f"代码 {code} {name} {rank_type} 排名{pos} "
                                   f"{'连续' + str(days) + '天 ' if days > 0 else ''}"
                                   f"涨跌幅:{chg:.2f}% 换手率:{turnover:.2f}%",
                        ))
                except Exception:
                    continue

            time.sleep(0.3)
        except Exception as e:
            logger.debug(f"THS强势股 {rank_type} 失败: {e}")

    rows._strong_stock_rank = strong_data
    logger.info(f"THS强势股结构化: {len(rows)} 条信号, {len(strong_data)} 条排名数据")
    return rows


# ============================================================
#  概念板块行情 (存储到 concept_board_quotes 表)
# ============================================================

def fetch_concept_board_quotes(make_signal) -> list:
    """同花顺概念板块行情 -- stock_board_concept_name_ths

    Returns make_signal() for top concepts + attaches _concept_board_quotes data.
    """
    import akshare as ak
    import pandas as pd
    rows = []
    concept_data = []
    today = datetime.now(BEIJING_TZ).date()

    try:
        logger.debug("概念板块行情: stock_board_concept_name_ths")
        df = ak.stock_board_concept_name_ths()
        if df is None or not hasattr(df, 'empty') or df.empty:
            return rows

        col_name = next((c for c in ['概念名称', '板块名称', 'name'] if c in df.columns), None)
        col_chg = next((c for c in ['涨跌幅', '涨跌幅(%)', 'pct_chg'] if c in df.columns), None)
        col_code = next((c for c in ['代码', '板块代码', 'code'] if c in df.columns), None)
        col_turnover = next((c for c in ['换手率', 'turnover'] if c in df.columns), None)
        col_stock_count = next((c for c in ['公司家数', '股票数量', 'stock_count'] if c in df.columns), None)
        col_lead_stock = next((c for c in ['领涨股票', 'lead_stock'] if c in df.columns), None)
        col_lead_code = next((c for c in ['领涨股票代码', 'lead_stock_code'] if c in df.columns), None)

        if not col_name:
            return rows

        if col_chg:
            df[col_chg] = pd.to_numeric(df[col_chg], errors='coerce')
            df_sorted = df.sort_values(col_chg, ascending=False)
        else:
            df_sorted = df

        for pos, (_, r) in enumerate(df_sorted.head(50).iterrows(), 1):
            try:
                concept = str(r.get(col_name, '') or '')
                if not concept or concept == 'nan':
                    continue

                chg = float(r.get(col_chg, 0) or 0) if col_chg else 0
                concept_code = str(r.get(col_code, '') or '') if col_code else ''
                turnover = float(r.get(col_turnover, 0) or 0) if col_turnover else 0
                stock_count = int(r.get(col_stock_count, 0) or 0) if col_stock_count else 0
                lead_name = str(r.get(col_lead_stock, '') or '') if col_lead_stock else ''
                lead_code_raw = str(r.get(col_lead_code, '') or '') if col_lead_code else ''
                lead_code = re.sub(r'[^0-9]', '', lead_code_raw).zfill(6) if lead_code_raw else ''
                lead_ts = f"{lead_code}.SH" if lead_code.startswith(('6', '688')) else f"{lead_code}.SZ" if lead_code else ''

                concept_data.append({
                    'trade_date': today,
                    'concept_code': concept_code or f"CONCEPT_{pos:04d}",
                    'concept_name': concept,
                    'pct_chg': chg,
                    'turnover_rate': turnover,
                    'lead_stock_code': lead_ts,
                    'lead_stock_name': lead_name,
                    'stock_count': stock_count,
                })

                if pos <= 10:
                    rows.append(make_signal(
                        source='THS-概念板块', tier=2,
                        title=f"概念: {concept} 涨{chg:.2f}%",
                        content=f"概念板块 {concept} 涨跌幅:{chg:.2f}% 换手率:{turnover:.2f}% "
                               f"领涨:{lead_name}({lead_ts}) 家数:{stock_count}",
                    ))
            except Exception:
                continue

    except Exception as e:
        logger.debug(f"概念板块行情失败: {e}")

    rows._concept_board_quotes = concept_data
    logger.info(f"概念板块行情: {len(rows)} 条信号, {len(concept_data)} 条板块数据")
    return rows


# ============================================================
#  机构一致预期 (存储到 earnings_forecast 表)
# ============================================================

def fetch_earnings_forecast_structured(make_signal) -> list:
    """机构一致预期 -- stock_rank_forecast_cninfo

    Returns make_signal() + attaches _earnings_forecast data.
    """
    import akshare as ak
    rows = []
    forecast_data = []

    try:
        logger.debug("机构一致预期: stock_rank_forecast_cninfo")
        df = ak.stock_rank_forecast_cninfo()
        if df is None or not hasattr(df, 'empty') or df.empty:
            return rows

        col_code = next((c for c in ['证券代码', '代码', 'code'] if c in df.columns), None)
        col_name = next((c for c in ['证券简称', '名称', 'name'] if c in df.columns), None)
        col_date = next((c for c in ['发布日期', 'date'] if c in df.columns), None)
        col_agency = next((c for c in ['研究机构简称', 'agency'] if c in df.columns), None)
        col_rating = next((c for c in ['投资评级', 'rating'] if c in df.columns), None)
        col_target_low = next((c for c in ['目标价格-下限'] if c in df.columns), None)
        col_target_high = next((c for c in ['目标价格-上限'] if c in df.columns), None)

        if not col_code:
            return rows

        for _, r in df.head(200).iterrows():
            try:
                raw_code = str(r.get(col_code, '') or '')
                code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
                if not code or len(code) < 6:
                    continue

                name = str(r.get(col_name, '') or '') if col_name else ''
                ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                agency = str(r.get(col_agency, '') or '') if col_agency else ''
                rating = str(r.get(col_rating, '') or '') if col_rating else ''

                rows.append(make_signal(
                    source='CNINFO-机构预测', tier=1,
                    title=f"机构预测: {name} {ts} {agency} {rating}",
                    content=f"代码 {code} {name} 机构:{agency} 评级:{rating}",
                ))
            except Exception:
                continue

    except Exception as e:
        logger.debug(f"机构一致预期失败: {e}")

    rows._earnings_forecast = forecast_data
    logger.info(f"机构一致预期: {len(rows)} 条信号")
    return rows


# ============================================================
#  Tencent 腾讯财经 补充数据 (PE/PB/市值/涨跌停价)
# ============================================================

def fetch_tencent_supplementary(make_signal, get_db_func=None) -> list:
    """腾讯财经补充数据 -- qt.gtimg.cn

    Fetches PE, PB, market cap, limit up/down prices.
    Stores to daily_quotes via UPDATE (returned as separate data).
    Returns make_signal() tuples for notable value stocks (low PE/PB).
    Pattern: same as zuiyou1.py get_realtime_quotes() but extracts extra fields.
    """
    rows = []
    tencent_data = []  # structured data for daily_quotes update

    # Get stock list from daily_quotes
    try:
        if get_db_func is None:
            from core.db.connection import get_db as get_db_func
        conn = get_db_func()
        cur = conn.cursor()
        cur.execute("""
            SELECT ts_code FROM daily_quotes
            WHERE trade_date = (SELECT MAX(trade_date) FROM daily_QUOTES)
            ORDER BY amount DESC NULLS LAST
            LIMIT 500;
        """)
        codes = [row[0] for row in cur.fetchall()]
        cur.close(); conn.close()
    except Exception as e:
        logger.debug(f"Tencent: 无法获取股票列表: {e}")
        return rows

    if not codes:
        return rows

    # Convert ts_code to tencent format (sh600519 / sz000001)
    def to_tencent_code(ts):
        parts = ts.split('.')
        if len(parts) != 2:
            return None
        code, market = parts
        if market == 'SH':
            return f"sh{code}"
        elif market == 'SZ':
            return f"sz{code}"
        return None

    batch_size = 50
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        tencent_codes = [to_tencent_code(c) for c in batch if to_tencent_code(c)]
        if not tencent_codes:
            continue

        code_str = ','.join(tencent_codes)
        try:
            url = f"http://qt.gtimg.cn/q={code_str}"
            resp = requests.get(url, timeout=10)
            resp.encoding = 'gbk'
            text = resp.text

            for line in text.strip().split('\n'):
                try:
                    if '~' not in line:
                        continue
                    p = line.split('~')
                    if len(p) < 49:
                        continue

                    name = p[1].strip()
                    code6 = p[2].strip()
                    now_price = float(p[3] or 0)
                    pre_close = float(p[4] or 0)
                    volume = float(p[6] or 0)
                    pct_chg = float(p[32] or 0)
                    high = float(p[33] or 0)
                    turnover = float(p[38] or 0)

                    # Supplementary fields
                    pe = float(p[39] or 0) if p[39] else 0
                    mcap = float(p[44] or 0) if p[44] else 0  # 总市值(亿)
                    circ_mcap = float(p[45] or 0) if p[45] else 0  # 流通市值(亿)
                    pb = float(p[46] or 0) if p[46] else 0

                    # Limit up/down prices
                    limit_up = float(p[47] or 0) if len(p) > 47 and p[47] else 0
                    limit_down = float(p[48] or 0) if len(p) > 48 and p[48] else 0

                    # NEW fields: amplitude, volume_ratio, commission_ratio, large_order_net, main_force_net
                    # p[43]=振幅, p[49]=量比, p[50]=委比, p[51]=大单净量, p[52]=主力净流入
                    amplitude = float(p[43] or 0) if len(p) > 43 and p[43] else 0
                    volume_ratio = float(p[49] or 0) if len(p) > 49 and p[49] else 0
                    commission_ratio = float(p[50] or 0) if len(p) > 50 and p[50] else 0
                    large_order_net = float(p[51] or 0) if len(p) > 51 and p[51] else 0
                    main_force_net = float(p[52] or 0) if len(p) > 52 and p[52] else 0

                    ts_code = f"{code6}.SH" if code6.startswith(('6', '688')) else f"{code6}.SZ"

                    tencent_data.append({
                        'ts_code': ts_code,
                        'pe_ratio': pe,
                        'pb_ratio': pb,
                        'total_market_cap': mcap * 1e8,  # 亿 -> 元
                        'circulating_market_cap': circ_mcap * 1e8,
                        'limit_up_price': limit_up,
                        'limit_down_price': limit_down,
                        'amplitude': amplitude,
                        'volume_ratio': volume_ratio,
                        'commission_ratio': commission_ratio,
                        'large_order_net': large_order_net,
                        'main_force_net': main_force_net,
                    })

                    # Generate signals for notable value stocks
                    if 0 < pe < 10 and pb < 1.5 and mcap > 50:
                        rows.append(make_signal(
                            source='Tencent-行情补充', tier=1,
                            title=f"低估值: {name} {ts_code} PE:{pe:.1f} PB:{pb:.2f}",
                            content=f"代码 {code6} {name} PE:{pe:.1f} PB:{pb:.2f} "
                                   f"总市值:{mcap:.0f}亿 换手率:{turnover:.2f}%",
                        ))

                except (ValueError, IndexError):
                    continue

            time.sleep(0.12)

        except Exception as e:
            logger.debug(f"Tencent batch request failed: {e}")
            continue

    # Store tencent data for structured storage
    rows._tencent_data = tencent_data

    logger.info(f"Tencent行情补充: {len(rows)} 条信号, {len(tencent_data)} 条估值数据")
    return rows


# ============================================================
#  同花顺 THS 强势股
# ============================================================

def fetch_ths_strong_stocks(make_signal) -> list:
    """同花顺强势股 -- stock_rank_lxsz_ths, stock_rank_cxg_ths, stock_rank_ljqd_ths

    Multi-interface:
    1. stock_rank_lxsz_ths() -- 连续上涨
    2. stock_rank_cxg_ths(symbol='创月新高') -- 创新高
    3. stock_rank_ljqd_ths() -- 量价齐升
    """
    import akshare as ak
    rows = []

    fetchers = [
        ('连续上涨', lambda: ak.stock_rank_lxsz_ths()),
        ('创月新高', lambda: ak.stock_rank_cxg_ths(symbol='创月新高')),
        ('量价齐升', lambda: ak.stock_rank_ljqd_ths()),
    ]

    for label, fetcher in fetchers:
        try:
            logger.debug(f"THS强势股: {label}")
            df = fetcher()
            if df is None or not hasattr(df, 'empty') or df.empty:
                logger.debug(f"THS {label}: 空数据")
                continue

            col_code = next((c for c in ['代码', '股票代码', 'code'] if c in df.columns), None)
            col_name = next((c for c in ['名称', '股票名称', 'name'] if c in df.columns), None)
            col_days = next((c for c in ['连续上涨天数', '天数', 'days'] if c in df.columns), None)
            col_chg = next((c for c in ['涨跌幅', '累计涨跌幅', 'pct_chg'] if c in df.columns), None)
            col_turnover = next((c for c in ['换手率', 'turnover'] if c in df.columns), None)

            if not col_code:
                logger.debug(f"THS {label} 列不全: {list(df.columns)[:10]}")
                continue

            for _, r in df.head(30).iterrows():
                try:
                    raw_code = str(r.get(col_code, '') or '')
                    code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
                    if not code or code == 'nan' or len(code) < 6:
                        continue

                    name = str(r.get(col_name, '') or '') if col_name else ''
                    ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                    days = int(r.get(col_days, 0) or 0) if col_days else 0
                    chg = float(r.get(col_chg, 0) or 0) if col_chg else 0
                    turnover = float(r.get(col_turnover, 0) or 0) if col_turnover else 0

                    detail_parts = [f"{label}"]
                    if days > 0:
                        detail_parts.append(f"{days}天")
                    if chg != 0:
                        detail_parts.append(f"涨{chg:.2f}%")
                    if turnover > 0:
                        detail_parts.append(f"换手{turnover:.2f}%")

                    rows.append(make_signal(
                        source='THS-强势股', tier=1,
                        title=f"强势: {name} {ts} {' '.join(detail_parts)}",
                        content=f"代码 {code} {name} {label} "
                               f"{'连续' + str(days) + '天 ' if days > 0 else ''}"
                               f"涨跌幅:{chg:.2f}% 换手率:{turnover:.2f}%",
                    ))
                except Exception:
                    continue

            logger.info(f"THS {label}: {len([r for r in rows])} 条")
            time.sleep(0.3)

        except AttributeError as e:
            logger.debug(f"THS {label} 接口不存在: {e}")
        except Exception as e:
            logger.debug(f"THS {label} 失败: {e}")

    logger.info(f"THS强势股合计: {len(rows)} 条")
    return rows


# ============================================================
#  同花顺 THS 概念标签
# ============================================================

def fetch_ths_concept_tags(make_signal) -> list:
    """同花顺概念标签 -- stock_board_concept_name_ths + stock_board_concept_info_ths

    Fetches concept board data and constituent stock mappings.
    Returns make_signal() for top-moving concepts.
    """
    import akshare as ak
    rows = []

    try:
        logger.debug("THS概念: stock_board_concept_name_ths")
        df = ak.stock_board_concept_name_ths()
        if df is None or not hasattr(df, 'empty') or df.empty:
            logger.debug("THS概念: 空数据")
            return rows

        col_name = next((c for c in ['概念名称', '板块名称', 'name'] if c in df.columns), None)
        col_chg = next((c for c in ['涨跌幅', '涨跌幅(%)', 'pct_chg'] if c in df.columns), None)
        col_code = next((c for c in ['代码', '概念代码', 'code'] if c in df.columns), None)

        if not col_name:
            logger.warning(f"THS概念列不全: {list(df.columns)[:10]}")
            return rows

        # Sort by change, take top 10
        if col_chg:
            df[col_chg] = pd.to_numeric(df[col_chg], errors='coerce')
            df_sorted = df.nlargest(10, col_chg)
        else:
            df_sorted = df.head(10)

        for _, r in df_sorted.iterrows():
            try:
                concept = str(r.get(col_name, '') or '')
                if not concept or concept == 'nan':
                    continue
                chg = float(r.get(col_chg, 0) or 0) if col_chg else 0
                concept_code = str(r.get(col_code, '') or '') if col_code else ''

                rows.append(make_signal(
                    source='THS-概念标签', tier=2,
                    title=f"THS概念: {concept} 涨{chg:.2f}%",
                    content=f"THS概念板块 {concept} 涨跌幅:{chg:.2f}% 代码:{concept_code}",
                ))
            except Exception:
                continue

        logger.info(f"THS概念标签: {len(rows)} 条")

    except AttributeError as e:
        logger.debug(f"THS概念接口不存在: {e}")
    except Exception as e:
        logger.debug(f"THS概念失败: {e}")

    return rows


# ============================================================
#  MootDX 实时行情 (requires mootdx package)
# ============================================================

def _get_mootdx_client():
    """Create mootdx Quotes client with fallback.

    Returns Quotes.factory(market='std', timeout=5) or None on failure.
    """
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market='std', timeout=5)
        return client
    except ImportError:
        logger.debug("mootdx 未安装")
        return None
    except Exception as e:
        logger.debug(f"MootDX连接失败: {e}")
        return None


def fetch_mootdx_realtime(make_signal, codes=None) -> list:
    """实时行情 via mootdx -- client.quotes()

    Fetches top-200 stocks by default.
    Returns make_signal() tuples for notable movers.
    Connection failure: returns [], does NOT crash.
    """
    rows = []
    client = _get_mootdx_client()
    if client is None:
        return rows

    try:
        import pandas as pd
        if codes is None:
            # Get top-200 from daily_quotes
            try:
                from core.db.connection import get_db
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    SELECT ts_code FROM daily_quotes
                    WHERE trade_date = (SELECT MAX(trade_date) FROM daily_quotes)
                    ORDER BY amount DESC NULLS LAST
                    LIMIT 200;
                """)
                codes = [row[0] for row in cur.fetchall()]
                cur.close(); conn.close()
            except Exception:
                return rows

        if not codes:
            return rows

        # Convert to mootdx format: (market, code)
        # market: 0=SZ, 1=SH
        symbols = []
        for ts in codes:
            parts = ts.split('.')
            if len(parts) == 2:
                code, market = parts
                mkt = 1 if market == 'SH' else 0
                symbols.append((mkt, code))

        # mootdx quotes() takes list of (market, code)
        try:
            result = client.quotes(symbol=symbols)
            if result is not None and hasattr(result, 'empty') and not result.empty:
                for _, r in result.iterrows():
                    try:
                        code = str(r.get('code', '')).zfill(6)
                        mkt = int(r.get('market', 0))
                        ts = f"{code}.{'SH' if mkt == 1 else 'SZ'}"
                        close = float(r.get('close', 0) or 0)
                        pct = float(r.get('percent', 0) or 0)

                        if abs(pct) >= 5:  # Only signal notable movers
                            name = str(r.get('name', '') or '')
                            rows.append(make_signal(
                                source='MootDX-实时行情', tier=1,
                                title=f"MootDX: {name} {ts} {'+' if pct > 0 else ''}{pct:.2f}%",
                                content=f"代码 {code} {name} 收盘:{close:.3f} 涨跌幅:{pct:.2f}%",
                            ))
                    except Exception:
                        continue

                logger.info(f"MootDX实时行情: {len(rows)} 条信号 (from {len(symbols)} stocks)")
        except Exception as e:
            logger.debug(f"MootDX quotes 失败: {e}")

    except Exception as e:
        logger.debug(f"MootDX实时行情失败: {e}")

    return rows


def fetch_mootdx_orderbook(make_signal, codes=None) -> list:
    """五档盘口 via mootdx -- client.quotes() (bid1-5/ask1-5 are in response)

    Only during trading hours (9:30-15:00 Beijing time).
    Returns empty list outside trading hours.
    """
    rows = []
    now = datetime.now(BEIJING_TZ)
    hour, minute = now.hour, now.minute

    # Only during trading hours
    if not (9 <= hour < 15) or (hour == 9 and minute < 30):
        return rows

    client = _get_mootdx_client()
    if client is None:
        return rows

    try:
        if codes is None:
            try:
                from core.db.connection import get_db
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    SELECT ts_code FROM daily_quotes
                    WHERE trade_date = (SELECT MAX(trade_date) FROM daily_quotes)
                    ORDER BY amount DESC NULLS LAST
                    LIMIT 50;
                """)
                codes = [row[0] for row in cur.fetchall()]
                cur.close(); conn.close()
            except Exception:
                return rows

        if not codes:
            return rows

        symbols = []
        for ts in codes:
            parts = ts.split('.')
            if len(parts) == 2:
                code, market = parts
                mkt = 1 if market == 'SH' else 0
                symbols.append((mkt, code))

        try:
            result = client.quotes(symbol=symbols)
            if result is not None and hasattr(result, 'empty') and not result.empty:
                # mootdx quotes response includes bid1_vol, bid1_price, etc.
                for _, r in result.iterrows():
                    try:
                        code = str(r.get('code', '')).zfill(6)
                        mkt = int(r.get('market', 0))
                        ts = f"{code}.{'SH' if mkt == 1 else 'SZ'}"

                        # Extract order book data if available
                        bid1_vol = int(r.get('bid_vol1', 0) or 0)
                        ask1_vol = int(r.get('ask_vol1', 0) or 0)

                        if bid1_vol > 0 or ask1_vol > 0:
                            rows.append({
                                'ts_code': ts,
                                'bid1_price': float(r.get('bid1', 0) or 0),
                                'bid1_vol': bid1_vol,
                                'ask1_price': float(r.get('ask1', 0) or 0),
                                'ask1_vol': ask1_vol,
                            })
                    except Exception:
                        continue

                logger.info(f"MootDX盘口: {len(rows)} 条")
        except Exception as e:
            logger.debug(f"MootDX盘口 quotes 失败: {e}")

    except Exception as e:
        logger.debug(f"MootDX盘口失败: {e}")

    return rows


def fetch_mootdx_kline(make_signal, codes=None, frequency='d') -> list:
    """K线数据 via mootdx -- client.bars()

    frequency: 'd' (daily), 'w' (weekly), 'm' (monthly)
    Default: fetches last 60 daily bars for top-50 stocks.
    """
    rows = []
    client = _get_mootdx_client()
    if client is None:
        return rows

    try:
        if codes is None:
            try:
                from core.db.connection import get_db
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    SELECT ts_code FROM daily_quotes
                    WHERE trade_date = (SELECT MAX(trade_date) FROM daily_quotes)
                    ORDER BY amount DESC NULLS LAST
                    LIMIT 50;
                """)
                codes = [row[0] for row in cur.fetchall()]
                cur.close(); conn.close()
            except Exception:
                return rows

        if not codes:
            return rows

        for ts in codes[:50]:
            try:
                parts = ts.split('.')
                if len(parts) != 2:
                    continue
                code, market = parts
                mkt = 1 if market == 'SH' else 0

                df = client.bars(symbol=code, market=mkt, frequency=frequency, count=60)
                if df is not None and hasattr(df, 'empty') and not df.empty:
                    # K-line data is stored for analysis, not as raw_signals
                    # Just count successful fetches
                    rows.append({'ts_code': ts, 'bars': len(df)})
                time.sleep(0.1)
            except Exception:
                continue

        logger.info(f"MootDX K线: {len(rows)} 只股票获取成功")

    except Exception as e:
        logger.debug(f"MootDX K线失败: {e}")

    return rows
