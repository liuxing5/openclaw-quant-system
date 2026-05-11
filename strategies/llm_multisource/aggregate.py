"""每日候选池生成 - 盘前/盘后双跑"""
import os, sys, math
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from datetime import date, timedelta, datetime, timezone
from psycopg2.extras import RealDictCursor
from loguru import logger

from core.db.connection import get_db
from core.db.candidates import write_candidates
from core.utils.env import load_project_env
from core.utils.trading_calendar import is_trading_day as _calendar_is_trading_day

load_project_env()

RUN_MODE = os.getenv('RUN_MODE', 'morning')
MIN_SELECT_SCORE = int(os.getenv('MIN_SELECT_SCORE', '25'))
MAX_SELECTED = 5
MIN_LIQUIDITY = 1e8
SOURCE = 'llm_multisource'

# 用集合判断，兼容多种命名
AFTERHOURS_MODES = {'afterhours', 'afternoon', 'evening'}

BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date():
    return datetime.now(BEIJING_TZ).date()


def is_trading_day(d):
    """判断是否为交易日。委托给 core.utils.trading_calendar，含节假日识别。"""
    return _calendar_is_trading_day(d)


def calc_price_levels(close, ts_code):
    """根据板块计算入场/止损/目标价"""
    if not close:
        return None
    code = ts_code.split('.')[0]
    is_kc_cy = code.startswith(('688', '300', '301'))
    
    if is_kc_cy:
        return {
            'entry_low': round(close * 0.985, 2),
            'entry_high': round(close * 1.015, 2),
            'stop_loss': round(close * 0.95, 2),
            'target_1': round(close * 1.08, 2),
            'target_2': round(close * 1.15, 2),
        }
    else:
        return {
            'entry_low': round(close * 0.99, 2),
            'entry_high': round(close * 1.01, 2),
            'stop_loss': round(close * 0.97, 2),
            'target_1': round(close * 1.05, 2),
            'target_2': round(close * 1.10, 2),
        }


def aggregate_today():
    today = get_beijing_date()
    
    # 非交易日跳过
    if not is_trading_day(today):
        logger.warning(f"{today} 非交易日，跳过候选生成")
        return
    
    cutoff = today - timedelta(days=2)
    logger.info(f"=== 开始聚合，today={today}, cutoff={cutoff}, RUN_MODE={RUN_MODE} ===")

    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)

    # 跑前清空当天同 run_mode 同 source 的旧数据，避免重复
    # 注意：故意不在此处 commit。DELETE 与后续 write_candidates 的 INSERT 必须
    # 在同一事务里。write_candidates 内部会在所有 INSERT 完成后统一 commit；
    # 若中途异常或行情取数失败提前 return，连接关闭时事务自动 rollback，
    # 已有的旧候选不会被错误清空。
    cur.execute("""
        DELETE FROM daily_candidates
        WHERE snapshot_date = %s AND run_mode = %s AND source = %s;
    """, (today, RUN_MODE, SOURCE))
    deleted = cur.rowcount
    if deleted > 0:
        logger.info(f"将清空 {deleted} 条今天的旧候选数据（提交时机：write_candidates 成功后）")

    cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
    row = cur.fetchone()
    latest_trade_date = row['max_date'] if row else None
    if not latest_trade_date:
        logger.error("daily_quotes 表为空，无法获取行情数据")
        cur.close(); conn.close()
        return
    logger.info(f"数据库最新交易日: {latest_trade_date}，使用此日期加载行情")

    cur.execute("""
        SELECT ts_code, close, pct_chg, turnover_rate, amount
        FROM daily_quotes WHERE trade_date=%s;
    """, (latest_trade_date,))
    quote_cache = {}
    for q in cur.fetchall():
        quote_cache[q['ts_code']] = {
            'close': float(q['close']) if q['close'] else None,
            'pct_chg': float(q['pct_chg']) if q['pct_chg'] else 0,
            'turnover_rate': float(q['turnover_rate']) if q['turnover_rate'] else 0,
            'amount': float(q['amount']) if q['amount'] else 0,
        }
    logger.info(f"加载 {len(quote_cache)} 条行情到缓存 (trade_date={latest_trade_date})")
    
    # 加载股票名称缓存
    cur.execute("SELECT ts_code, stock_name FROM stock_basic_info;")
    name_cache = {r['ts_code']: r['stock_name'] for r in cur.fetchall()}
    logger.info(f"加载 {len(name_cache)} 条股票名称到缓存")

    cur.execute("""
        SELECT
          e.ts_code, MAX(e.stock_name) AS stock_name,
          COUNT(*) AS mention_count,
          COUNT(DISTINCT 
            CASE 
                WHEN e.source_name IN ('AKShare-龙虎榜', 'AKShare-涨停板') THEN 'capital'
                WHEN e.source_name = 'AKShare-个股研报' THEN 'research'
                WHEN e.source_name = 'AKShare-机构调研' THEN 'institution'
                WHEN e.source_name IN ('AKShare-财经新闻', 'AKShare-热点概念') THEN 'news'
                ELSE 'other'
            END
          ) AS source_diversity,
          COUNT(DISTINCT e.source_name) AS source_count_raw,
          AVG(e.strength * COALESCE(e.confidence,0.5)) AS llm_score,
          ARRAY_AGG(DISTINCT e.logic_category) AS logic_tags,
          JSON_AGG(JSON_BUILD_OBJECT(
            'source', e.source_name, 'tier', COALESCE(fs.tier, 2), 'strength', e.strength,
            'logic', e.logic_summary, 'pub_time', e.pub_time
          )) AS sources
        FROM extracted_recommendations e
        LEFT JOIN feed_sources fs ON fs.name = e.source_name
        WHERE e.pub_time >= %s
          AND e.recommendation_type IN ('buy','strong_buy','watch')
          AND e.strength >= 2
          AND (
            -- 涨停板信号只看 24 小时内（昨日涨停今天不该当推荐依据）
            (e.source_name = 'AKShare-涨停板' AND e.pub_time >= NOW() - INTERVAL '20 hours')
            OR e.source_name != 'AKShare-涨停板'
          )
        GROUP BY e.ts_code
        HAVING COUNT(*) >= 1;
    """, (cutoff,))
    rows = cur.fetchall()
    logger.info(f"从 extracted_recommendations 查询到 {len(rows)} 条记录 (cutoff={cutoff})")

    candidates = []

    if rows:
        logger.info(f"从 LLM 提取到 {len(rows)} 只推荐")
        rows = [r for r in rows if r['ts_code'].endswith(('.SH', '.SZ'))]
        rows = [r for r in rows if not (r['stock_name'] and 'ST' in r['stock_name'].upper())]

        for r in rows:
            q = quote_cache.get(r['ts_code'])

            if not q or not q.get('close'):
                logger.debug(f"{r['ts_code']} 无行情，跳过")
                continue

            close_price = float(q['close'])
            amount = float(q.get('amount') or 0)
            pct_chg = q.get('pct_chg') or 0
            turnover = q.get('turnover_rate') or 0

            ts = r['ts_code']
            is_kc_cy = ts.split('.')[0].startswith(('688', '300', '301'))
            limit_threshold = 19.5 if is_kc_cy else 9.5

            if RUN_MODE in AFTERHOURS_MODES and pct_chg >= limit_threshold:
                logger.info(f"{ts} 今日已涨停 {pct_chg:.1f}%，盘后模式跳过（次日陷阱风险）")
                continue

            # 根据信号源类型给不同流动性门槛
            sources_in_signal = [s.get('source', '') for s in (r['sources'] or [])]
            has_research = any('研报' in s for s in sources_in_signal)
            has_zt = any('涨停' in s for s in sources_in_signal)
            
            # 涨停板的票要求流动性高（盘中博弈），研报票可以降低门槛（中长线持有）
            if has_zt and not has_research:
                min_liq = 1e8
            elif has_research:
                min_liq = 5e7  # 研报票门槛降到 5000 万
            else:
                min_liq = 1e8
            
            if amount < min_liq:
                logger.debug(f"{r['ts_code']} 流动性不足 {amount/1e8:.2f}亿 (要求 {min_liq/1e8:.1f}亿)")
                continue

            # 连续量化评分
            quant_score = 0
            
            # 涨幅：-3%到7%是连续函数，涨幅2%时加分最高
            if -3 < pct_chg < 7:
                quant_score += 30 * (1 - abs(pct_chg - 2) / 5)
            elif 7 <= pct_chg < limit_threshold:
                quant_score += 10
            
            # 换手率：超过3%加分，连续函数，15%换手到顶
            if turnover > 0:
                quant_score += min(30, turnover * 2)
            
            # 成交额：log函数，10亿对应40分
            if amount > 0:
                quant_score += min(40, 10 * math.log10(amount / 1e8 + 1))

            # 涨停惩罚
            if pct_chg > limit_threshold:
                quant_score = max(0, quant_score - 30)

            consensus = min(r['source_diversity'] / 2.0, 1.0)
            llm_n = min(r['llm_score'] / 5.0, 1.0)
            # 提及次数加成
            mention_bonus = min(0.2, r['mention_count'] * 0.03)
            llm_n = min(1.0, llm_n + mention_bonus)
            quant_n = quant_score / 100.0  # 满分改为100

            if llm_n > 0 and quant_n > 0:
                final = (llm_n ** 0.4) * (quant_n ** 0.6) * (0.5 + 0.5 * consensus) * 100
            else:
                final = 0

            has_lhb = any('龙虎榜' in (s.get('source') or '') for s in (r['sources'] or []))
            has_zt = any('涨停' in (s.get('source') or '') for s in (r['sources'] or []))
            if has_lhb and has_zt:
                if RUN_MODE == 'morning':
                    logger.info(f"{r['ts_code']} 龙虎榜+涨停板共振，盘前模式直接过滤")
                    continue
                elif RUN_MODE in AFTERHOURS_MODES:
                    final = final * 0.6
                    logger.debug(f"{r['ts_code']} 龙虎榜+涨停板共振，盘后降权 40%")

            candidates.append({
                'ts_code': r['ts_code'], 'stock_name': name_cache.get(r['ts_code']) or r['stock_name'],
                'mention_count': r['mention_count'], 'source_diversity': r['source_diversity'],
                'consensus_score': consensus, 'llm_score': llm_n * 100,
                'quant_score': quant_score, 'final_score': final,
                'logic_tags': r['logic_tags'], 'sources': r['sources'],
                'close': close_price,
                'pct_chg': pct_chg,
                'turnover_rate': turnover,
            })
    else:
        logger.info("无 LLM 推荐数据，使用纯量化选股")
        cur.execute("""
            SELECT ts_code, close, pct_chg, turnover_rate, amount, volume
            FROM daily_quotes
            WHERE trade_date = %s
              AND amount > 1e8
              AND pct_chg BETWEEN -3 AND 7
              AND turnover_rate > 3
            ORDER BY amount DESC
            LIMIT 50;
        """, (latest_trade_date,))
        quotes = cur.fetchall()

        for q in quotes:
            ts_code = q['ts_code']
            if not (ts_code.endswith('.SH') or ts_code.endswith('.SZ')):
                continue

            close_price = float(q['close']) if q['close'] else None
            pct_chg = q['pct_chg'] or 0
            turnover = q['turnover_rate'] or 0
            amount = q['amount'] or 0

            # 盘后模式跳过涨停股（和LLM分支保持一致）
            is_kc_cy = ts_code.split('.')[0].startswith(('688', '300', '301'))
            limit = 19.5 if is_kc_cy else 9.5
            if RUN_MODE in AFTERHOURS_MODES and pct_chg >= limit:
                continue

            # 连续量化评分（和LLM分支保持一致）
            quant_score = 0
            
            # 涨幅：-3%到7%是连续函数，涨幅2%时加分最高
            if -3 < pct_chg < 7:
                quant_score += 30 * (1 - abs(pct_chg - 2) / 5)
            elif 7 <= pct_chg < limit:
                quant_score += 10
            
            # 换手率：超过3%加分，连续函数，15%换手到顶
            if turnover > 0:
                quant_score += min(30, turnover * 2)
            
            # 成交额：log函数，10亿对应40分
            if amount > 0:
                quant_score += min(40, 10 * math.log10(amount / 1e8 + 1))

            # 涨停惩罚
            if pct_chg > limit:
                quant_score = max(0, quant_score - 30)

            final = quant_score * 0.8

            candidates.append({
                'ts_code': ts_code, 'stock_name': name_cache.get(ts_code, ''),
                'mention_count': 1, 'source_diversity': 1,
                'consensus_score': 0.3, 'llm_score': 0,
                'quant_score': quant_score, 'final_score': final,
                'logic_tags': ['量化选股'], 'sources': [{'source': 'quant', 'tier': 2}],
                'close': close_price,
                'pct_chg': pct_chg,
                'turnover_rate': turnover,
            })

    candidates.sort(key=lambda x: x['final_score'], reverse=True)
    logger.info(f"排序后候选池: {len(candidates)} 只")
    for c in candidates[:10]:
        logger.info(f"  {c['ts_code']} {c['stock_name'] or '?'} final={c['final_score']:.1f} llm={c['llm_score']:.0f} quant={c['quant_score']:.0f} consensus={c['consensus_score']:.2f}")

    if RUN_MODE == 'intraday':
        filtered = []
        for c in candidates:
            ts = c['ts_code']
            pct = c.get('pct_chg', 0)
            is_kc_cy = ts.split('.')[0].startswith(('688', '300', '301'))
            limit = 19.5 if is_kc_cy else 9.5

            if pct >= limit:
                logger.debug(f"{ts} 已涨停 {pct}%，盘中跳过")
                continue

            if 4 < pct < limit and c.get('turnover_rate', 0) > 5:
                c['final_score'] *= 1.2
                if 'logic_tags' not in c:
                    c['logic_tags'] = []
                c['logic_tags'].append('准涨停')

            filtered.append(c)
        candidates = filtered
        logger.info(f"盘中模式过滤后剩余 {len(candidates)} 只候选")

    qualified = [c for c in candidates if c['final_score'] >= MIN_SELECT_SCORE]

    if not qualified:
        logger.warning(f"无候选股达到阈值 {MIN_SELECT_SCORE}")
        observation = candidates[:10]
        for c in observation:
            c['selected'] = False
            c['position_pct'] = 0
            levels = calc_price_levels(c.get('close'), c['ts_code'])
            if levels:
                c.update(levels)
        n = write_candidates(observation, today, source=SOURCE, run_mode=RUN_MODE, conn=conn)
        cur.close(); conn.close()
        logger.info(f"写入 {n} 条观察记录到 daily_candidates (snapshot_date={today})")
        return

    selected_list = qualified[:MAX_SELECTED]
    logger.info(f"合格 {len(qualified)} 只，选中 {len(selected_list)} 只")
    for c in selected_list:
        logger.info(f"  选中: {c['ts_code']} {c['stock_name']} final={c['final_score']:.1f}")

    items = []
    for c in qualified[:15]:
        is_selected = c in selected_list
        levels = calc_price_levels(c['close'], c['ts_code'])
        c['selected'] = is_selected
        c['position_pct'] = 0.08 if is_selected else 0
        if levels:
            c.update(levels)
        items.append(c)

    n = write_candidates(items, today, source=SOURCE, run_mode=RUN_MODE, conn=conn)
    cur.close(); conn.close()
    logger.info(f"候选池生成完毕，snapshot_date={today}，共写入 {n} 条记录")


if __name__ == '__main__':
    aggregate_today()
