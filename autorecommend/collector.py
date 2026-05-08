"""信息采集器 - GHA 模式（AKShare only）"""
import os
import re
import time
import hashlib
import warnings
import threading
import concurrent.futures
from datetime import date, timedelta, datetime
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from loguru import logger
from dotenv import load_dotenv

warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', message='.*invalid escape sequence.*')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

FETCH_TIMEOUT = 30
CONCEPT_TIMEOUT = 20


def fetch_with_timeout(fetcher_func, timeout=FETCH_TIMEOUT):
    """带超时的采集包装器"""
    result = [None]
    error = [None]

    def target():
        try:
            result[0] = fetcher_func()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        logger.warning(f"{fetcher_func.__name__} 超时 ({timeout}s)，跳过")
        return []
    if error[0]:
        logger.warning(f"{fetcher_func.__name__} 异常: {error[0]}")
        return []
    return result[0] or []


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
    )


def make_signal(source, title, content, url='', pub_time=None, tier=2):
    """构造 raw_signal 入库行"""
    h = hashlib.md5(f"{source}|{title}|{content[:200]}".encode()).hexdigest()
    return (
        None, source, tier, title[:1000],
        (content or '')[:5000], url[:500],
        pub_time or datetime.now(), datetime.now(), h,
    )


def fetch_akshare_news():
    """AKShare 财经新闻 - 多接口兜底"""
    import akshare as ak
    rows = []
    for fetch_func in [
        lambda: ak.news_cctv(),
        lambda: ak.stock_info_cjzc_em(),
    ]:
        try:
            df = fetch_func()
            if df is None or not hasattr(df, 'empty') or df.empty:
                continue
            col_title = next((c for c in ['title', '标题', '新闻标题'] if c in df.columns), None)
            col_content = next((c for c in ['content', '内容', '新闻内容', 'summary'] if c in df.columns), None)
            col_time = next((c for c in ['date', 'pub_time', '发布时间', '时间'] if c in df.columns), None)
            col_url = next((c for c in ['url', 'link', '链接'] if c in df.columns), None)
            if not col_title:
                continue
            for _, r in df.iterrows():
                try:
                    title = str(r.get(col_title, '') or '')
                    if not title or title == 'nan' or len(title) < 5:
                        continue
                    content = str(r.get(col_content, '') or '') if col_content else ''
                    rows.append(make_signal(
                        source='AKShare-财经新闻', tier=2,
                        title=title[:1000],
                        content=content[:5000],
                        url=str(r.get(col_url, '') or '')[:500] if col_url else '',
                        pub_time=pd.to_datetime(r.get(col_time), errors='coerce') if col_time else None,
                    ))
                except Exception:
                    continue
            if rows:
                logger.info(f"财经新闻: {len(rows)} 条")
                return rows
        except Exception as e:
            logger.debug(f"news 接口失败: {e}")
            continue
    logger.warning("所有新闻接口均失败")
    return rows


def fetch_akshare_lhb():
    """龙虎榜 -> 信号"""
    import akshare as ak
    today_str = date.today().strftime('%Y%m%d')
    rows = []
    try:
        df = ak.stock_lhb_detail_em(start_date=today_str, end_date=today_str)
        if df is None:
            logger.warning("lhb: 接口返回 None")
            return rows
        if not hasattr(df, 'empty') or df.empty:
            logger.info("lhb: 今日无数据")
            return rows
        col_code = next((c for c in ['代码', '股票代码', 'code'] if c in df.columns), None)
        col_name = next((c for c in ['名称', '股票名称', 'name'] if c in df.columns), None)
        col_reason = next((c for c in ['上榜原因', '解读', 'reason'] if c in df.columns), None)
        col_net = next((c for c in ['龙虎榜净买额', '净买额', '净额'] if c in df.columns), None)
        if not col_code:
            logger.warning(f"lhb: 找不到代码列，可用列: {list(df.columns)}")
            return rows
        for _, r in df.iterrows():
            try:
                code = str(r.get(col_code, '') or '').zfill(6)
                if not code or code == 'nan' or len(code) < 4:
                    continue
                name = r.get(col_name, '') or '' if col_name else ''
                ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                net = r.get(col_net, 0) or 0 if col_net else 0
                reason = r.get(col_reason, '') or '' if col_reason else ''
                rows.append(make_signal(
                    source='AKShare-龙虎榜', tier=1,
                    title=f"龙虎榜: {name} {ts} 净买入{net/1e8:.2f}亿",
                    content=f"代码 {code} {name} 上榜原因: {reason} 龙虎榜净买额 {net} 元",
                ))
            except Exception:
                continue
        logger.info(f"龙虎榜: {len(rows)} 条")
    except Exception as e:
        logger.warning(f"lhb 失败: {e}")
    return rows


def fetch_akshare_zt_pool():
    """涨停板池 -> 信号"""
    import akshare as ak
    today_str = date.today().strftime('%Y%m%d')
    rows = []
    try:
        df = ak.stock_zt_pool_em(date=today_str)
        if df is None or not hasattr(df, 'empty') or df.empty:
            logger.info("涨停板: 无数据")
            return rows
        for _, r in df.iterrows():
            code = str(r.get('代码', '')).zfill(6)
            name = r.get('名称', '')
            ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
            rows.append(make_signal(
                source='AKShare-涨停板', tier=1,
                title=f"涨停: {name} {ts} 连板{r.get('连板数', 1)}",
                content=f"代码 {code} {name} 封板时间 {r.get('首次封板时间', '')} "
                       f"涨停统计 {r.get('涨停统计', '')} 所属行业 {r.get('所属行业', '')}",
            ))
        logger.info(f"涨停板: {len(rows)} 条")
    except Exception as e:
        logger.warning(f"zt_pool 失败: {e}")
    return rows


def fetch_akshare_concept_hot():
    """概念板块异动 -> 信号"""
    import akshare as ak
    rows = []
    try:
        df = ak.stock_board_concept_name_em()
        df_sorted = df.sort_values('涨跌幅', ascending=False).head(10)
        for _, r in df_sorted.iterrows():
            concept = r.get('板块名称', '')
            chg = r.get('涨跌幅', 0)
            if chg < 1.5:
                continue
            rows.append(make_signal(
                source='AKShare-热点概念', tier=2,
                title=f"概念异动: {concept} 涨{chg:.2f}%",
                content=f"{concept} 板块今日涨幅 {chg:.2f}%",
            ))
        logger.info(f"热点概念: {len(rows)} 条")
    except Exception as e:
        logger.warning(f"concept 失败: {e}")
    return rows


def fetch_akshare_research():
    """个股研报 - 多接口兜底"""
    import akshare as ak
    rows = []
    for func_name, kwargs in [
        ('stock_research_report_em', {'symbol': '全部'}),
        ('stock_industry_research_em', {}),
    ]:
        try:
            func = getattr(ak, func_name, None)
            if not func:
                continue
            df = func(**kwargs) if kwargs else func()
            if df is None or not hasattr(df, 'empty') or df.empty:
                logger.debug(f"research {func_name}: 无数据")
                continue
            col_code = next((c for c in ['股票代码', '代码', 'symbol'] if c in df.columns), None)
            col_name = next((c for c in ['股票简称', '股票名称', '名称'] if c in df.columns), None)
            col_date = next((c for c in ['日期', '发布日期', 'date'] if c in df.columns), None)
            col_rating = next((c for c in ['评级', '最新评级', 'rating'] if c in df.columns), None)
            col_org = next((c for c in ['机构名称', '机构', 'org'] if c in df.columns), None)
            col_target = next((c for c in ['目标价', 'target_price'] if c in df.columns), None)
            col_title = next((c for c in ['报告名称', '标题', 'title'] if c in df.columns), None)
            if not col_code or not col_date:
                logger.warning(f"research {func_name}: 列不全 {list(df.columns)[:10]}")
                continue
            df[col_date] = pd.to_datetime(df[col_date], errors='coerce')
            cutoff = pd.Timestamp(date.today() - timedelta(days=3))
            df = df[df[col_date] >= cutoff]
            for _, r in df.iterrows():
                try:
                    code = str(r.get(col_code, '') or '').zfill(6)
                    if not code or code == 'nan' or len(code) < 6:
                        continue
                    name = r.get(col_name, '') or '' if col_name else ''
                    ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                    rating = r.get(col_rating, '') or '' if col_rating else ''
                    org = r.get(col_org, '') or '' if col_org else ''
                    target_price = r.get(col_target, '') or '' if col_target else ''
                    report_title = r.get(col_title, '') or '' if col_title else ''
                    rows.append(make_signal(
                        source='AKShare-个股研报', tier=1,
                        title=f"研报: {name} {ts} {rating} - {org}",
                        content=f"代码 {code} {name} 评级 {rating} 目标价 {target_price} "
                               f"报告 {report_title} 机构 {org}",
                        pub_time=r[col_date],
                    ))
                except Exception:
                    continue
            logger.info(f"个股研报 ({func_name}): {len(rows)} 条")
            if rows:
                return rows
        except Exception as e:
            logger.warning(f"research {func_name} 失败: {e}")
            continue
    return rows


def fetch_akshare_jgdy():
    """机构调研 - 按日期分页，只取最近 3 天"""
    import akshare as ak
    rows = []
    try:
        target_dates = [(date.today() - timedelta(days=i)).strftime('%Y%m%d') for i in range(3)]
        for d in target_dates:
            try:
                df = ak.stock_jgdy_detail_em(date=d)
                if df is None or not hasattr(df, 'empty') or df.empty:
                    logger.debug(f"jgdy {d}: 无数据")
                    continue
                col_code = next((c for c in ['股票代码', '代码'] if c in df.columns), None)
                col_name = next((c for c in ['股票简称', '名称'] if c in df.columns), None)
                col_count = next((c for c in ['接待机构数量', '机构数'] if c in df.columns), None)
                if not col_code:
                    logger.debug(f"jgdy {d}: 列不全 {list(df.columns)[:10]}")
                    continue
                for _, r in df.iterrows():
                    code = str(r.get(col_code, '') or '').zfill(6)
                    if not code or len(code) < 6:
                        continue
                    name = r.get(col_name, '') or '' if col_name else ''
                    count = r.get(col_count, 0) or 0 if col_count else 0
                    ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                    rows.append(make_signal(
                        source='AKShare-机构调研', tier=1,
                        title=f"机构调研: {name} {ts} - {count}家",
                        content=f"代码 {code} {name} 接待 {count} 家机构 调研日期 {d}",
                    ))
            except Exception as e:
                logger.debug(f"jgdy {d} 失败: {e}")
                continue
        logger.info(f"机构调研: {len(rows)} 条")
    except Exception as e:
        logger.warning(f"jgdy 失败: {e}")
    return rows


def store_signals(rows):
    """批量入库（去重靠 content_hash）"""
    if not rows:
        return 0
    conn = get_db(); cur = conn.cursor()
    inserted = 0
    for row in rows:
        try:
            cur.execute("""
                INSERT INTO raw_signals
                (source_id, source_name, source_tier, title, content, url, pub_time, fetch_time, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (content_hash) DO NOTHING;
            """, row)
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            logger.debug(f"insert err: {e}")
            conn.rollback()
            continue
    conn.commit()
    cur.close(); conn.close()
    return inserted


def main():
    log_file = os.path.join(BASE_DIR, 'logs', 'collector.log')
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger.add(log_file, rotation='100 MB')

    logger.info("=" * 60)
    logger.info("AKShare 信息采集启动（并行模式）")
    logger.info("=" * 60)

    fetchers = [
        (fetch_akshare_news, '财经新闻', FETCH_TIMEOUT),
        (fetch_akshare_lhb, '龙虎榜', FETCH_TIMEOUT),
        (fetch_akshare_zt_pool, '涨停板', FETCH_TIMEOUT),
        (fetch_akshare_concept_hot, '热点概念', CONCEPT_TIMEOUT),
        (fetch_akshare_research, '个股研报', FETCH_TIMEOUT),
        (fetch_akshare_jgdy, '机构调研', FETCH_TIMEOUT),
    ]

    all_rows = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {}
        for fetcher, name, timeout_override in fetchers:
            future = executor.submit(fetch_with_timeout, fetcher, timeout_override)
            future_map[future] = name

        for future in concurrent.futures.as_completed(future_map):
            name = future_map[future]
            try:
                rows = future.result()
                all_rows.extend(rows)
                logger.info(f"{name}: {len(rows)} 条")
            except Exception as e:
                logger.error(f"{name} 完全失败: {e}")

    inserted = store_signals(all_rows)
    logger.info(f"采集总计 {len(all_rows)} 条，新入库 {inserted} 条")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    main()
