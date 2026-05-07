"""信息采集器 - GHA 模式（AKShare only）"""
import os
import re
import time
import hashlib
import warnings
import threading
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

FETCH_TIMEOUT = 60
CONCEPT_TIMEOUT = 30


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
    """AKShare 财经新闻"""
    import akshare as ak
    rows = []
    try:
        df = ak.stock_news_em()
        if df is None or df.empty:
            return rows
        for _, r in df.iterrows():
            try:
                rows.append(make_signal(
                    source='AKShare-财经新闻', tier=2,
                    title=str(r.get('新闻标题', ''))[:1000],
                    content=str(r.get('新闻内容', ''))[:5000],
                    url=str(r.get('新闻链接', ''))[:500],
                    pub_time=pd.to_datetime(r.get('发布时间')) if r.get('发布时间') else None,
                ))
            except Exception:
                continue
        logger.info(f"AKShare 财经新闻: {len(rows)} 条")
    except Exception as e:
        logger.warning(f"AKShare news 失败: {e}")
    return rows


def fetch_akshare_lhb():
    """龙虎榜 -> 信号"""
    import akshare as ak
    today_str = date.today().strftime('%Y%m%d')
    rows = []
    try:
        df = ak.stock_lhb_detail_em(start_date=today_str, end_date=today_str)
        for _, r in df.iterrows():
            code = str(r.get('代码', '')).zfill(6)
            name = r.get('名称', '')
            ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
            net = r.get('龙虎榜净买额', 0)
            rows.append(make_signal(
                source='AKShare-龙虎榜', tier=1,
                title=f"龙虎榜: {name} {ts} 净买入{net/1e8:.2f}亿",
                content=f"代码 {code} {name} 上榜原因: {r.get('上榜原因', '')} "
                       f"龙虎榜净买额 {net} 元",
            ))
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
    """个股研报"""
    import akshare as ak
    rows = []
    try:
        df = ak.stock_research_report_em(symbol="全部")
        if df is None or df.empty:
            return rows

        date_col = None
        for c in ['日期', 'report_date', 'date']:
            if c in df.columns:
                date_col = c
                break
        if not date_col:
            logger.warning("research: 找不到日期列")
            return rows

        df[date_col] = pd.to_datetime(df[date_col])
        cutoff = pd.Timestamp(date.today() - timedelta(days=3))
        df = df[df[date_col] >= cutoff]

        for _, r in df.iterrows():
            code = str(r.get('股票代码', '')).zfill(6)
            if not code or code == 'nan' or len(code) < 4:
                continue
            name = r.get('股票简称', '')
            ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
            rows.append(make_signal(
                source='AKShare-个股研报', tier=1,
                title=f"研报: {name} {ts} {r.get('评级', '')} - {r.get('机构名称', '')}",
                content=f"代码 {code} {name} 评级 {r.get('评级', '')} "
                       f"目标价 {r.get('目标价', '')} 报告标题 {r.get('报告名称', '')} "
                       f"机构 {r.get('机构名称', '')}",
                pub_time=r[date_col],
            ))
        logger.info(f"个股研报: {len(rows)} 条")
    except Exception as e:
        logger.warning(f"research 失败: {e}")
    return rows


def fetch_akshare_jgdy():
    """机构调研"""
    import akshare as ak
    rows = []
    try:
        df = ak.stock_jgdy_detail_em()
        if df is None or df.empty:
            return rows
        cutoff = date.today() - timedelta(days=7)
        date_col = None
        for c in ['调研日期', 'date', 'report_date']:
            if c in df.columns:
                date_col = c
                break
        if date_col:
            df[date_col] = pd.to_datetime(df[date_col])
            df = df[df[date_col] >= pd.Timestamp(cutoff)]
        for _, r in df.iterrows():
            code = str(r.get('股票代码', '')).zfill(6)
            if not code or code == 'nan' or len(code) < 4:
                continue
            name = r.get('股票简称', '')
            ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
            rows.append(make_signal(
                source='AKShare-机构调研', tier=1,
                title=f"机构调研: {name} {ts} - {r.get('接待机构数量', 0)}家",
                content=f"代码 {code} {name} 接待 {r.get('接待机构数量', 0)} 家机构 "
                       f"调研日期 {r.get(date_col, '')}",
            ))
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
    logger.info("AKShare 信息采集启动")
    logger.info("=" * 60)

    all_rows = []

    for fetcher, name, timeout_override in [
        (fetch_akshare_news, '财经新闻', FETCH_TIMEOUT),
        (fetch_akshare_lhb, '龙虎榜', FETCH_TIMEOUT),
        (fetch_akshare_zt_pool, '涨停板', FETCH_TIMEOUT),
        (fetch_akshare_concept_hot, '热点概念', CONCEPT_TIMEOUT),
        (fetch_akshare_research, '个股研报', FETCH_TIMEOUT),
        (fetch_akshare_jgdy, '机构调研', FETCH_TIMEOUT),
    ]:
        try:
            rows = fetch_with_timeout(fetcher, timeout=timeout_override)
            all_rows.extend(rows)
            time.sleep(1)
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
