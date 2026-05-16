"""Layer 3: News sources -- CLS (财联社) telegraph via akshare"""
import time
from datetime import datetime, timedelta, timezone
from loguru import logger

BEIJING_TZ = timezone(timedelta(hours=8))


def fetch_cls_telegraph(make_signal) -> list:
    """财联社电报 -- akshare stock_info_global_cls

    Returns make_signal() tuples with source='财联社-电报', tier=1.
    Multi-interface fallback:
      1. ak.stock_info_global_cls(symbol='全部')
      2. ak.stock_info_global_cls(symbol='重点')
    """
    import akshare as ak
    rows = []

    for symbol in ['全部', '重点']:
        try:
            logger.debug(f"CLS 接口: stock_info_global_cls(symbol='{symbol}')")
            df = ak.stock_info_global_cls(symbol=symbol)
            if df is None or not hasattr(df, 'empty') or df.empty:
                logger.debug(f"CLS '{symbol}' 返回空")
                continue

            col_title = next((c for c in ['标题', 'title'] if c in df.columns), None)
            col_content = next((c for c in ['内容', 'content', '摘要'] if c in df.columns), None)
            col_date = next((c for c in ['发布日期', '日期', 'date'] if c in df.columns), None)
            col_time = next((c for c in ['发布时间', '时间', 'time'] if c in df.columns), None)

            if not col_title:
                logger.warning(f"CLS '{symbol}' 找不到标题列, 可用: {list(df.columns)}")
                continue

            for _, r in df.iterrows():
                try:
                    title = str(r.get(col_title, '') or '')
                    if not title or title == 'nan' or len(title) < 5:
                        continue
                    content = str(r.get(col_content, '') or '') if col_content else ''

                    # Parse pub_time from date + time columns
                    pub_time = None
                    if col_date and col_time:
                        date_str = str(r.get(col_date, '') or '')
                        time_str = str(r.get(col_time, '') or '')
                        try:
                            pub_time = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
                            pub_time = pub_time.replace(tzinfo=BEIJING_TZ)
                        except Exception:
                            pass

                    rows.append(make_signal(
                        source='财联社-电报', tier=1,
                        title=title[:1000],
                        content=content[:5000],
                        pub_time=pub_time,
                    ))
                except Exception:
                    continue

            if rows:
                logger.info(f"财联社电报 ({symbol}): {len(rows)} 条")
                return rows
            time.sleep(0.3)

        except AttributeError as e:
            logger.debug(f"CLS '{symbol}' 接口不存在: {e}")
        except Exception as e:
            logger.debug(f"CLS '{symbol}' 失败: {e}")

    logger.info(f"财联社电报: {len(rows)} 条")
    return rows
