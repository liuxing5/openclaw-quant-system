"""信息采集器 - GHA 模式（AKShare only）"""
import os
import re
import time
import hashlib
import warnings
import threading
import concurrent.futures
from datetime import date, timedelta, datetime, timezone
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from loguru import logger
from dotenv import load_dotenv

warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', message='.*invalid escape sequence.*')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

FETCH_TIMEOUT = 120
CONCEPT_TIMEOUT = 90

# 北京时间时区
BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date():
    """获取北京时间日期（解决 GitHub Actions UTC 时区问题）"""
    return datetime.now(BEIJING_TZ).date()


def fetch_with_timeout(fetcher_func, timeout=FETCH_TIMEOUT, max_retries=2):
    """带超时的采集包装器（支持重试）"""
    for attempt in range(max_retries + 1):
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
            if attempt < max_retries:
                logger.warning(f"{fetcher_func.__name__} 超时 ({timeout}s)，第 {attempt + 1} 次重试...")
                time.sleep(2)
                continue
            else:
                logger.warning(f"{fetcher_func.__name__} 超时 ({timeout}s)，已重试 {max_retries} 次，跳过")
                return []
        if error[0]:
            if attempt < max_retries:
                logger.warning(f"{fetcher_func.__name__} 异常: {error[0]}，第 {attempt + 1} 次重试...")
                time.sleep(2)
                continue
            else:
                logger.warning(f"{fetcher_func.__name__} 异常: {error[0]}，已重试 {max_retries} 次")
                return []
        return result[0] or []
    return []


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
    """AKShare 财经新闻 - 多接口兜底，确保拿到真实数据"""
    import akshare as ak
    rows = []
    today = get_beijing_date()
    logger.info(f"财经新闻采集，日期: {today}")
    
    # 接口1: news_cctv (央视新闻)
    try:
        logger.debug("news 接口1: news_cctv")
        df = ak.news_cctv()
        if df is not None and hasattr(df, 'empty') and not df.empty:
            logger.info(f"news_cctv 返回 {len(df)} 条")
            col_title = next((c for c in ['title', '标题', '新闻标题'] if c in df.columns), None)
            col_content = next((c for c in ['content', '内容', '新闻内容', 'summary'] if c in df.columns), None)
            col_time = next((c for c in ['date', 'pub_time', '发布时间', '时间'] if c in df.columns), None)
            col_url = next((c for c in ['url', 'link', '链接'] if c in df.columns), None)
            if col_title:
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
                    logger.info(f"财经新闻 (cctv): {len(rows)} 条")
                    return rows
    except Exception as e:
        logger.debug(f"news_cctv 失败: {e}")
    
    # 接口2: stock_info_cjzc_em (财经资讯)
    try:
        logger.debug("news 接口2: stock_info_cjzc_em")
        df = ak.stock_info_cjzc_em()
        if df is not None and hasattr(df, 'empty') and not df.empty:
            logger.info(f"stock_info_cjzc_em 返回 {len(df)} 条")
            col_title = next((c for c in ['title', '标题', '新闻标题'] if c in df.columns), None)
            col_content = next((c for c in ['content', '内容', '新闻内容', 'summary'] if c in df.columns), None)
            col_time = next((c for c in ['date', 'pub_time', '发布时间', '时间'] if c in df.columns), None)
            col_url = next((c for c in ['url', 'link', '链接'] if c in df.columns), None)
            if col_title:
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
                    logger.info(f"财经新闻 (cjzc): {len(rows)} 条")
                    return rows
    except Exception as e:
        logger.debug(f"stock_info_cjzc_em 失败: {e}")
    
    # 接口3: stock_news_em (东方财富新闻)
    try:
        logger.debug("news 接口3: stock_news_em")
        df = ak.stock_news_em(symbol="全部")
        if df is not None and hasattr(df, 'empty') and not df.empty:
            logger.info(f"stock_news_em 返回 {len(df)} 条")
            col_title = next((c for c in ['title', '标题', '新闻标题'] if c in df.columns), None)
            col_content = next((c for c in ['content', '内容', '新闻内容', 'summary'] if c in df.columns), None)
            col_time = next((c for c in ['date', 'pub_time', '发布时间', '时间'] if c in df.columns), None)
            col_url = next((c for c in ['url', 'link', '链接'] if c in df.columns), None)
            if col_title:
                for _, r in df.head(50).iterrows():
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
                    logger.info(f"财经新闻 (em): {len(rows)} 条")
                    return rows
    except Exception as e:
        logger.debug(f"stock_news_em 失败: {e}")
    
    logger.warning("所有新闻接口均失败")
    return rows


def fetch_akshare_lhb():
    """龙虎榜 -> 信号，多接口多日期兜底，确保拿到真实数据"""
    import akshare as ak
    today_str = get_beijing_date().strftime('%Y%m%d')
    rows = []
    logger.info(f"龙虎榜采集，日期: {today_str}")
    
    # 接口1: stock_lhb_detail_em (龙虎榜详情) - 尝试最近3天
    for date_str in [today_str, (get_beijing_date() - timedelta(days=1)).strftime('%Y%m%d'), (get_beijing_date() - timedelta(days=2)).strftime('%Y%m%d'), (get_beijing_date() - timedelta(days=3)).strftime('%Y%m%d')]:
        try:
            logger.debug(f"lhb 接口1: {date_str}")
            df = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
            if df is not None and hasattr(df, 'empty') and not df.empty:
                logger.info(f"lhb detail {date_str} 返回 {len(df)} 条")
                col_code = next((c for c in ['代码', '股票代码', 'code'] if c in df.columns), None)
                col_name = next((c for c in ['名称', '股票名称', 'name'] if c in df.columns), None)
                col_reason = next((c for c in ['上榜原因', '解读', 'reason'] if c in df.columns), None)
                col_net = next((c for c in ['龙虎榜净买额', '净买额', '净额'] if c in df.columns), None)
                if col_code:
                    for _, r in df.iterrows():
                        try:
                            raw_code = str(r.get(col_code, '') or '')
                            code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
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
                    if rows:
                        logger.info(f"龙虎榜 (detail {date_str}): {len(rows)} 条")
                        return rows
        except Exception as e:
            logger.debug(f"lhb 接口1 {date_str} 失败: {e}")
    
    # 接口2: stock_lhb_ggtj_em (龙虎榜个股统计)
    try:
        logger.debug("lhb 接口2: stock_lhb_ggtj_em")
        df = ak.stock_lhb_ggtj_em(start_date=today_str, end_date=today_str)
        if df is not None and hasattr(df, 'empty') and not df.empty:
            logger.info(f"lhb ggtj 返回 {len(df)} 条")
            col_code = next((c for c in ['代码', '股票代码'] if c in df.columns), None)
            col_name = next((c for c in ['名称', '股票名称'] if c in df.columns), None)
            col_net = next((c for c in ['净买额', '龙虎榜净买额'] if c in df.columns), None)
            if col_code:
                for _, r in df.iterrows():
                    try:
                        raw_code = str(r.get(col_code, '') or '')
                        code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
                        if not code or code == 'nan' or len(code) < 4:
                            continue
                        name = r.get(col_name, '') or '' if col_name else ''
                        ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                        net = r.get(col_net, 0) or 0 if col_net else 0
                        rows.append(make_signal(
                            source='AKShare-龙虎榜', tier=1,
                            title=f"龙虎榜: {name} {ts} 净买入{net/1e8:.2f}亿",
                            content=f"代码 {code} {name} 龙虎榜净买额 {net} 元",
                        ))
                    except Exception:
                        continue
                if rows:
                    logger.info(f"龙虎榜 (ggtj): {len(rows)} 条")
                    return rows
    except Exception as e:
        logger.debug(f"lhb 接口2 失败: {e}")
    
    # 接口3: stock_lhb_jgzz_em (龙虎榜机构追踪)
    try:
        logger.debug("lhb 接口3: stock_lhb_jgzz_em")
        df = ak.stock_lhb_jgzz_em(start_date=today_str, end_date=today_str)
        if df is not None and hasattr(df, 'empty') and not df.empty:
            logger.info(f"lhb jgzz 返回 {len(df)} 条")
            col_code = next((c for c in ['代码', '股票代码'] if c in df.columns), None)
            col_name = next((c for c in ['名称', '股票名称'] if c in df.columns), None)
            if col_code:
                for _, r in df.iterrows():
                    try:
                        raw_code = str(r.get(col_code, '') or '')
                        code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
                        if not code or code == 'nan' or len(code) < 4:
                            continue
                        name = r.get(col_name, '') or '' if col_name else ''
                        ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                        rows.append(make_signal(
                            source='AKShare-龙虎榜', tier=1,
                            title=f"龙虎榜: {name} {ts} 机构关注",
                            content=f"代码 {code} {name} 龙虎榜机构关注",
                        ))
                    except Exception:
                        continue
                if rows:
                    logger.info(f"龙虎榜 (jgzz): {len(rows)} 条")
                    return rows
    except Exception as e:
        logger.debug(f"lhb 接口3 失败: {e}")
    
    # 接口4: stock_lhb_hyyyb_em (龙虎榜活跃营业部)
    try:
        logger.debug("lhb 接口4: stock_lhb_hyyyb_em")
        df = ak.stock_lhb_hyyyb_em(start_date=today_str, end_date=today_str)
        if df is not None and hasattr(df, 'empty') and not df.empty:
            logger.info(f"lhb hyyyb 返回 {len(df)} 条")
            for _, r in df.head(10).iterrows():
                try:
                    title = str(r.get('营业部名称', '') or '')
                    if title and title != 'nan':
                        rows.append(make_signal(
                            source='AKShare-龙虎榜', tier=2,
                            title=f"龙虎榜活跃营业部: {title}",
                            content=f"龙虎榜活跃营业部: {title}",
                        ))
                except Exception:
                    continue
            if rows:
                logger.info(f"龙虎榜 (hyyyb): {len(rows)} 条")
                return rows
    except Exception as e:
        logger.debug(f"lhb 接口4 失败: {e}")
    
    logger.info(f"龙虎榜: {len(rows)} 条")
    return rows


def fetch_akshare_zt_pool():
    """涨停板池 -> 信号，多接口兜底，确保拿到真实数据"""
    import akshare as ak
    today_str = get_beijing_date().strftime('%Y%m%d')
    rows = []
    
    # 接口1: stock_zt_pool_em (涨停板池)
    try:
        logger.debug("zt_pool 接口1: stock_zt_pool_em")
        df = ak.stock_zt_pool_em(date=today_str)
        if df is not None and hasattr(df, 'empty') and not df.empty:
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
            if rows:
                logger.info(f"涨停板 (pool): {len(rows)} 条")
                return rows
    except Exception as e:
        logger.debug(f"zt_pool 接口1 失败: {e}")
    
    # 接口2: stock_zt_pool_previous_em (昨日涨停板)
    try:
        logger.debug("zt_pool 接口2: stock_zt_pool_previous_em")
        df = ak.stock_zt_pool_previous_em(date=today_str)
        if df is not None and hasattr(df, 'empty') and not df.empty:
            for _, r in df.iterrows():
                code = str(r.get('代码', '')).zfill(6)
                name = r.get('名称', '')
                ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                rows.append(make_signal(
                    source='AKShare-涨停板', tier=1,
                    title=f"涨停: {name} {ts} 连板{r.get('连板数', 1)}",
                    content=f"代码 {code} {name} 涨停统计 {r.get('涨停统计', '')} 所属行业 {r.get('所属行业', '')}",
                ))
            if rows:
                logger.info(f"涨停板 (previous): {len(rows)} 条")
                return rows
    except Exception as e:
        logger.debug(f"zt_pool 接口2 失败: {e}")
    
    # 接口3: stock_zt_pool_strong_em (强势涨停板)
    try:
        logger.debug("zt_pool 接口3: stock_zt_pool_strong_em")
        df = ak.stock_zt_pool_strong_em(date=today_str)
        if df is not None and hasattr(df, 'empty') and not df.empty:
            for _, r in df.iterrows():
                code = str(r.get('代码', '')).zfill(6)
                name = r.get('名称', '')
                ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                rows.append(make_signal(
                    source='AKShare-涨停板', tier=1,
                    title=f"涨停: {name} {ts} 强势涨停",
                    content=f"代码 {code} {name} 所属行业 {r.get('所属行业', '')}",
                ))
            if rows:
                logger.info(f"涨停板 (strong): {len(rows)} 条")
                return rows
    except Exception as e:
        logger.debug(f"zt_pool 接口3 失败: {e}")
    
    # 接口4: stock_zt_pool_sub_new_em (次新股涨停)
    try:
        logger.debug("zt_pool 接口4: stock_zt_pool_sub_new_em")
        df = ak.stock_zt_pool_sub_new_em(date=today_str)
        if df is not None and hasattr(df, 'empty') and not df.empty:
            for _, r in df.iterrows():
                code = str(r.get('代码', '')).zfill(6)
                name = r.get('名称', '')
                ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                rows.append(make_signal(
                    source='AKShare-涨停板', tier=1,
                    title=f"涨停: {name} {ts} 次新涨停",
                    content=f"代码 {code} {name} 所属行业 {r.get('所属行业', '')}",
                ))
            if rows:
                logger.info(f"涨停板 (sub_new): {len(rows)} 条")
                return rows
    except Exception as e:
        logger.debug(f"zt_pool 接口4 失败: {e}")
    
    logger.info(f"涨停板: {len(rows)} 条")
    return rows


def fetch_akshare_concept_hot():
    """概念板块异动 -> 信号，多接口兜底，确保获取当日真实数据"""
    import akshare as ak
    rows = []
    today_str = get_beijing_date().strftime('%Y%m%d')
    logger.info(f"热点概念采集，日期: {today_str}")
    
    # 接口1: stock_board_concept_name_em - 获取概念板块实时行情
    try:
        logger.debug("尝试接口1: stock_board_concept_name_em")
        df = ak.stock_board_concept_name_em()
        logger.debug(f"接口1返回: {type(df)}, shape={df.shape if df is not None else 'None'}")
        if df is not None and hasattr(df, 'empty') and not df.empty:
            logger.debug(f"接口1列名: {list(df.columns)[:15]}")
            # 查找涨跌幅列
            col_chg = next((c for c in ['涨跌幅', '涨跌幅 (%)', 'change_pct'] if c in df.columns), None)
            col_name = next((c for c in ['板块名称', '名称', 'concept_name'] if c in df.columns), None)
            
            if col_chg and col_name:
                df_valid = df[df[col_chg].notna()].copy()
                df_valid[col_chg] = pd.to_numeric(df_valid[col_chg], errors='coerce')
                df_sorted = df_valid.sort_values(col_chg, ascending=False).head(15)
                
                for _, r in df_sorted.iterrows():
                    concept = r.get(col_name, '')
                    chg = r.get(col_chg, 0)
                    try:
                        chg = float(chg)
                    except (ValueError, TypeError):
                        continue
                    if chg < 1.0:  # 降低阈值到 1%
                        continue
                    rows.append(make_signal(
                        source='AKShare-热点概念', tier=2,
                        title=f"概念异动: {concept} 涨{chg:.2f}%",
                        content=f"{concept} 板块今日涨幅 {chg:.2f}%",
                    ))
                if rows:
                    logger.info(f"热点概念 (board): {len(rows)} 条")
                    return rows
            else:
                logger.warning(f"接口1找不到列，col_name={col_name}, col_chg={col_chg}, 可用列: {list(df.columns)[:15]}")
        else:
            logger.warning(f"接口1返回空数据: {df}")
    except Exception as e:
        logger.warning(f"接口1失败: {e}")
    
    # 接口2: stock_board_hot_em - 热门概念板块
    try:
        logger.debug("尝试接口2: stock_board_hot_em")
        df = ak.stock_board_hot_em()
        logger.debug(f"接口2返回: {type(df)}, shape={df.shape if df is not None else 'None'}")
        if df is not None and hasattr(df, 'empty') and not df.empty:
            logger.debug(f"接口2列名: {list(df.columns)[:15]}")
            col_chg = next((c for c in ['涨跌幅', '涨跌幅 (%)', 'change_pct'] if c in df.columns), None)
            col_name = next((c for c in ['板块名称', '名称', 'concept_name'] if c in df.columns), None)
            
            if col_chg and col_name:
                df_valid = df[df[col_chg].notna()].copy()
                df_valid[col_chg] = pd.to_numeric(df_valid[col_chg], errors='coerce')
                df_sorted = df_valid.sort_values(col_chg, ascending=False).head(15)
                
                for _, r in df_sorted.iterrows():
                    concept = r.get(col_name, '')
                    chg = r.get(col_chg, 0)
                    try:
                        chg = float(chg)
                    except (ValueError, TypeError):
                        continue
                    if chg < 1.0:
                        continue
                    rows.append(make_signal(
                        source='AKShare-热点概念', tier=2,
                        title=f"概念异动: {concept} 涨{chg:.2f}%",
                        content=f"{concept} 板块今日涨幅 {chg:.2f}%",
                    ))
                if rows:
                    logger.info(f"热点概念 (hot): {len(rows)} 条")
                    return rows
    except Exception as e:
        logger.warning(f"接口2失败: {e}")
    
    # 接口3: stock_changes_em - 个股异动（作为最后兜底）
    try:
        logger.debug("尝试接口3: stock_changes_em")
        df = ak.stock_changes_em(symbol="全部")
        logger.debug(f"接口3返回: {type(df)}, shape={df.shape if df is not None else 'None'}")
        if df is not None and hasattr(df, 'empty') and not df.empty:
            logger.debug(f"接口3列名: {list(df.columns)[:15]}")
            col_reason = next((c for c in ['异动类型', '异动原因', 'reason'] if c in df.columns), None)
            col_name = next((c for c in ['名称', '股票名称'] if c in df.columns), None)
            col_code = next((c for c in ['代码', '股票代码'] if c in df.columns), None)
            
            if col_reason:
                for _, r in df.head(20).iterrows():
                    reason = r.get(col_reason, '')
                    name = r.get(col_name, '') if col_name else ''
                    raw_code = str(r.get(col_code, '') or '') if col_code else ''
                    code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
                    if code and code != 'nan' and len(code) >= 4 and reason:
                        ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                        rows.append(make_signal(
                            source='AKShare-热点概念', tier=2,
                            title=f"个股异动: {name} {ts} {reason}",
                            content=f"{name} 异动类型: {reason}",
                        ))
                if rows:
                    logger.info(f"热点概念 (changes): {len(rows)} 条")
                    return rows
    except Exception as e:
        logger.warning(f"接口3失败: {e}")
    
    # 接口4: stock_board_concept_cons_em - 概念板块成分（获取热门概念的成分股）
    try:
        logger.debug("尝试接口4: stock_board_concept_cons_em")
        # 先获取概念列表，取前几个热门概念的成分股
        df_concepts = ak.stock_board_concept_name_em()
        if df_concepts is not None and hasattr(df_concepts, 'empty') and not df_concepts.empty:
            col_chg = next((c for c in ['涨跌幅', '涨跌幅 (%)', 'change_pct'] if c in df_concepts.columns), None)
            col_name = next((c for c in ['板块名称', '名称', 'concept_name'] if c in df_concepts.columns), None)
            if col_chg and col_name:
                df_concepts[col_chg] = pd.to_numeric(df_concepts[col_chg], errors='coerce')
                top_concepts = df_concepts.nlargest(5, col_chg)
                for _, concept_row in top_concepts.iterrows():
                    concept_name = concept_row.get(col_name, '')
                    try:
                        df_cons = ak.stock_board_concept_cons_em(symbol=concept_name)
                        if df_cons is not None and hasattr(df_cons, 'empty') and not df_cons.empty:
                            col_code = next((c for c in ['代码', '股票代码'] if c in df_cons.columns), None)
                            col_stock_name = next((c for c in ['名称', '股票名称'] if c in df_cons.columns), None)
                            if col_code and col_stock_name:
                                for _, r in df_cons.head(3).iterrows():
                                    raw_code = str(r.get(col_code, '') or '')
                                    code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
                                    stock_name = r.get(col_stock_name, '')
                                    if code and code != 'nan' and len(code) >= 4:
                                        ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                                        rows.append(make_signal(
                                            source='AKShare-热点概念', tier=2,
                                            title=f"热门概念成分: {concept_name} - {stock_name} {ts}",
                                            content=f"{concept_name} 概念成分股: {stock_name} {ts}",
                                        ))
                    except Exception as e:
                        logger.debug(f"获取 {concept_name} 成分股失败: {e}")
                        continue
                if rows:
                    logger.info(f"热点概念 (cons): {len(rows)} 条")
                    return rows
    except Exception as e:
        logger.warning(f"接口4失败: {e}")
    
    logger.info(f"热点概念: {len(rows)} 条")
    return rows


def fetch_akshare_research():
    """个股研报 - 多接口兜底"""
    import akshare as ak
    rows = []
    
    # 接口1: stock_research_report_em
    try:
        df = ak.stock_research_report_em(symbol='全部')
        if df is not None and hasattr(df, 'empty') and not df.empty:
            col_code = next((c for c in ['股票代码', '代码', 'symbol'] if c in df.columns), None)
            col_name = next((c for c in ['股票简称', '股票名称', '名称'] if c in df.columns), None)
            col_date = next((c for c in ['日期', '发布日期', 'date'] if c in df.columns), None)
            col_rating = next((c for c in ['评级', '最新评级', 'rating'] if c in df.columns), None)
            col_org = next((c for c in ['机构名称', '机构', 'org'] if c in df.columns), None)
            col_target = next((c for c in ['目标价', 'target_price'] if c in df.columns), None)
            col_title = next((c for c in ['报告名称', '标题', 'title'] if c in df.columns), None)
            if col_code and col_date:
                df[col_date] = pd.to_datetime(df[col_date], errors='coerce')
                cutoff = pd.Timestamp(get_beijing_date() - timedelta(days=3))
                df = df[df[col_date] >= cutoff]
                for _, r in df.iterrows():
                    try:
                        raw_code = str(r.get(col_code, '') or '')
                        code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
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
                    except Exception as e:
                        logger.debug(f"research 行处理失败: {e}")
                        continue
                if rows:
                    logger.info(f"个股研报 (report): {len(rows)} 条")
                    return rows
    except Exception as e:
        logger.debug(f"research report 失败: {e}")
    
    # 接口2: stock_info_global_em (全球个股研报)
    try:
        df = ak.stock_info_global_em()
        if df is not None and hasattr(df, 'empty') and not df.empty:
            col_title = next((c for c in ['标题', 'title', '报告名称'] if c in df.columns), None)
            col_content = next((c for c in ['内容', 'content', '摘要'] if c in df.columns), None)
            col_date = next((c for c in ['日期', 'date', '发布时间'] if c in df.columns), None)
            if col_title:
                for _, r in df.head(20).iterrows():
                    try:
                        title = str(r.get(col_title, '') or '')
                        if not title or title == 'nan' or len(title) < 5:
                            continue
                        content = str(r.get(col_content, '') or '') if col_content else ''
                        pub_time = pd.to_datetime(r.get(col_date), errors='coerce') if col_date else None
                        rows.append(make_signal(
                            source='AKShare-个股研报', tier=1,
                            title=f"研报: {title}",
                            content=content[:5000],
                            pub_time=pub_time,
                        ))
                    except Exception as e:
                        logger.debug(f"research global 行处理失败: {e}")
                        continue
                if rows:
                    logger.info(f"个股研报 (global): {len(rows)} 条")
                    return rows
    except Exception as e:
        logger.debug(f"research global 失败: {e}")
    
    logger.info(f"个股研报: {len(rows)} 条")
    return rows


def fetch_akshare_jgdy():
    """机构调研 - 多接口兜底，按日期分页，只取最近 5 天"""
    import akshare as ak
    rows = []
    try:
        # 尝试多个接口
        # 接口1: stock_jgdy_detail_em (按日期)
        target_dates = [(get_beijing_date() - timedelta(days=i)).strftime('%Y%m%d') for i in range(5)]
        for d in target_dates:
            try:
                df = ak.stock_jgdy_detail_em(date=d)
                if df is None:
                    logger.debug(f"jgdy detail {d}: 返回 None")
                    continue
                if not hasattr(df, 'empty') or df.empty:
                    logger.debug(f"jgdy detail {d}: 无数据")
                    continue
                col_code = next((c for c in ['股票代码', '代码'] if c in df.columns), None)
                col_name = next((c for c in ['股票简称', '名称'] if c in df.columns), None)
                col_count = next((c for c in ['接待机构数量', '机构数'] if c in df.columns), None)
                if not col_code:
                    logger.debug(f"jgdy detail {d}: 列不全 {list(df.columns)[:10]}")
                    continue
                for _, r in df.iterrows():
                    try:
                        raw_code = str(r.get(col_code, '') or '')
                        code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
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
                        logger.debug(f"jgdy detail {d} 行处理失败: {e}")
                        continue
                if rows:
                    logger.info(f"机构调研 (detail): {len(rows)} 条")
                    return rows
            except Exception as e:
                logger.debug(f"jgdy detail {d} 失败: {e}")
                continue
        
        # 接口2: stock_jgdy_summary_em (汇总)
        try:
            df = ak.stock_jgdy_summary_em()
            if df is not None and hasattr(df, 'empty') and not df.empty:
                col_code = next((c for c in ['股票代码', '代码'] if c in df.columns), None)
                col_name = next((c for c in ['股票简称', '名称'] if c in df.columns), None)
                col_count = next((c for c in ['接待机构数量', '机构数', '调研家数'] if c in df.columns), None)
                if col_code:
                    for _, r in df.iterrows():
                        try:
                            raw_code = str(r.get(col_code, '') or '')
                            code = re.sub(r'[^0-9]', '', raw_code).zfill(6)
                            if not code or len(code) < 6:
                                continue
                            name = r.get(col_name, '') or '' if col_name else ''
                            count = r.get(col_count, 0) or 0 if col_count else 0
                            ts = code + ('.SH' if code.startswith(('6', '688')) else '.SZ')
                            rows.append(make_signal(
                                source='AKShare-机构调研', tier=1,
                                title=f"机构调研: {name} {ts} - {count}家",
                                content=f"代码 {code} {name} 接待 {count} 家机构",
                            ))
                        except Exception as e:
                            logger.debug(f"jgdy summary 行处理失败: {e}")
                            continue
                    if rows:
                        logger.info(f"机构调研 (summary): {len(rows)} 条")
                        return rows
        except Exception as e:
            logger.debug(f"jgdy summary 失败: {e}")
    except Exception as e:
        logger.warning(f"jgdy 失败: {e}")
    logger.info(f"机构调研: {len(rows)} 条")
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
