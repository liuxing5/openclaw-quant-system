"""Layer 5: Announcements -- 巨潮 cninfo + mootdx"""
import re
import time
from datetime import datetime, timedelta, timezone
from loguru import logger

BEIJING_TZ = timezone(timedelta(hours=8))


def fetch_cninfo_announcements(make_signal) -> list:
    """巨潮公告 -- stock_zh_a_disclosure_report_cninfo

    Fetches recent announcements from cninfo for the most recent 3 days.
    Returns make_signal() tuples with source='巨潮-公告', tier=2.
    """
    import akshare as ak
    rows = []
    today = datetime.now(BEIJING_TZ).date()

    for day_offset in range(3):
        target_date = today - timedelta(days=day_offset)
        date_str = target_date.strftime('%Y%m%d')
        try:
            logger.debug(f"巨潮公告: {date_str}")
            df = ak.stock_zh_a_disclosure_report_cninfo(
                symbol='000001',
                market='沪深京',
                start_date=date_str,
                end_date=date_str,
            )
            if df is None or not hasattr(df, 'empty') or df.empty:
                continue

            col_title = next((c for c in ['公告标题', '标题', 'title'] if c in df.columns), None)
            col_code = next((c for c in ['代码', '证券代码', '股票代码', 'code'] if c in df.columns), None)
            col_name = next((c for c in ['简称', '证券简称', '名称', '股票名称', 'name'] if c in df.columns), None)
            col_time = next((c for c in ['公告时间', '发布时间', 'pub_date'] if c in df.columns), None)
            col_url = next((c for c in ['公告链接', '链接', 'url'] if c in df.columns), None)

            if not col_title:
                logger.warning(f"巨潮公告列不全: {list(df.columns)[:10]}")
                continue

            for _, r in df.head(50).iterrows():
                try:
                    title = str(r.get(col_title, '') or '')
                    if not title or title == 'nan' or len(title) < 5:
                        continue

                    raw_code = str(r.get(col_code, '') or '') if col_code else ''
                    code = re.sub(r'[^0-9]', '', raw_code).zfill(6) if raw_code else ''
                    name = str(r.get(col_name, '') or '') if col_name else ''
                    url = str(r.get(col_url, '') or '') if col_url else ''
                    pub_time = r.get(col_time) if col_time else None

                    ts = ''
                    if code and len(code) >= 6 and code != 'nan':
                        ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')

                    content = f"公告: {title}"
                    if ts:
                        content = f"代码 {ts} {name} 公告: {title}"

                    rows.append(make_signal(
                        source='巨潮-公告', tier=2,
                        title=title[:1000],
                        content=content[:5000],
                        url=url[:500],
                        pub_time=pub_time,
                    ))
                except Exception:
                    continue

            if rows:
                logger.info(f"巨潮公告 ({date_str}): {len(rows)} 条")
                break  # Got data, stop trying older dates
            time.sleep(0.5)

        except Exception as e:
            logger.debug(f"巨潮公告 {date_str} 失败: {e}")
            continue

    logger.info(f"巨潮公告: {len(rows)} 条")
    return rows


def fetch_mootdx_announcements(make_signal) -> list:
    """MootDX公告 -- client.announcement()

    Uses mootdx to fetch company announcements for top stocks.
    Falls back gracefully if mootdx connection fails.
    """
    rows = []

    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market='std', timeout=5)
        if client is None:
            logger.debug("MootDX公告: 连接失败")
            return rows

        # Fetch announcement list
        try:
            result = client.announcement()
            if result is not None and hasattr(result, 'empty') and not result.empty:
                for _, r in result.head(30).iterrows():
                    try:
                        title = str(r.get('title', '') or r.get('标题', '') or '')
                        if not title or len(title) < 5:
                            continue
                        rows.append(make_signal(
                            source='MootDX-公告', tier=2,
                            title=title[:1000],
                            content=title[:5000],
                        ))
                    except Exception:
                        continue
                logger.info(f"MootDX公告: {len(rows)} 条")
        except Exception as e:
            logger.debug(f"MootDX公告查询失败: {e}")

    except ImportError:
        logger.debug("mootdx 未安装，跳过 MootDX 公告")
    except Exception as e:
        logger.debug(f"MootDX公告失败: {e}")

    return rows
