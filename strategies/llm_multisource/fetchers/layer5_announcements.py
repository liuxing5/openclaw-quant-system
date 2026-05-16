from core.utils.ts_code import pure_to_ts_code
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
                market='沪深京',
                start_date=date_str,
                end_date=date_str,
            )
            if df is None:
                continue
            if not hasattr(df, 'empty') or not hasattr(df, 'columns'):
                logger.debug(f"巨潮公告 {date_str}: 返回非 DataFrame 类型")
                continue
            if df.empty:
                continue

            col_title = next((c for c in ['公告标题', '标题', 'title'] if c in df.columns), None)
            col_code = next((c for c in ['代码', '证券代码', '股票代码', 'code'] if c in df.columns), None)
            col_name = next((c for c in ['简称', '证券简称', '名称', '股票名称', 'name'] if c in df.columns), None)
            col_time = next((c for c in ['公告时间', '发布时间', 'pub_date'] if c in df.columns), None)
            col_url = next((c for c in ['公告链接', '链接', 'url'] if c in df.columns), None)

            if not col_title:
                logger.debug(f"巨潮公告 {date_str} 列不全: {list(df.columns)[:10]}")
                continue

            for _, r in df.head(50).iterrows():
                try:
                    if r is None:
                        continue
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
                        ts = pure_to_ts_code(code)

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
                break
            time.sleep(0.5)

        except Exception as e:
            logger.debug(f"巨潮公告 {date_str} 失败: {e}")
            continue

    logger.info(f"巨潮公告: {len(rows)} 条")
    return rows


def fetch_mootdx_announcements(make_signal) -> list:
    """mootdx 无公告查询接口，此 fetcher 已禁用。

    mootdx Quotes 对象不提供 announcement() 方法。可用方法:
      quotes(), bars(), finance(), xdxr(), gpjy(), index(), minute(),
      transaction(), history() —— 均与公告无关。
    唯一公告数据源是巨潮 cninfo (fetch_cninfo_announcements)。
    """
    return []
