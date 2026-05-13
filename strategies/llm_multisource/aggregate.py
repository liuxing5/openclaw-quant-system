"""每日候选池生成 - 盘前/盘后双跑"""
import json
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
        SELECT ts_code, close, pct_chg, turnover_rate, amount,
               amplitude, volume_ratio, commission_ratio, large_order_net, main_force_net
        FROM daily_quotes WHERE trade_date=%s;
    """, (latest_trade_date,))
    quote_cache = {}
    for q in cur.fetchall():
        quote_cache[q['ts_code']] = {
            'close': float(q['close']) if q['close'] else None,
            'pct_chg': float(q['pct_chg']) if q['pct_chg'] else 0,
            'turnover_rate': float(q['turnover_rate']) if q['turnover_rate'] else 0,
            'amount': float(q['amount']) if q['amount'] else 0,
            'amplitude': float(q['amplitude']) if q['amplitude'] else 0,
            'volume_ratio': float(q['volume_ratio']) if q['volume_ratio'] else 0,
            'commission_ratio': float(q['commission_ratio']) if q['commission_ratio'] else 0,
            'large_order_net': float(q['large_order_net']) if q['large_order_net'] else 0,
            'main_force_net': float(q['main_force_net']) if q['main_force_net'] else 0,
        }
    logger.info(f"加载 {len(quote_cache)} 条行情到缓存 (trade_date={latest_trade_date})")
    
    # 加载股票名称缓存
    cur.execute("SELECT ts_code, stock_name FROM stock_basic_info;")
    name_cache = {r['ts_code']: r['stock_name'] for r in cur.fetchall()}
    logger.info(f"加载 {len(name_cache)} 条股票名称到缓存")

    # 加载强势股排名数据
    cur.execute("""
        SELECT ts_code, rank_type, rank_position, consecutive_days,
               stage_chg_pct, cumulative_turnover, industry
        FROM strong_stock_rank WHERE trade_date=%s;
    """, (latest_trade_date,))
    strong_rank_cache = {}
    for r in cur.fetchall():
        ts = r['ts_code']
        if ts not in strong_rank_cache:
            strong_rank_cache[ts] = []
        strong_rank_cache[ts].append({
            'rank_type': r['rank_type'],
            'rank_position': r['rank_position'],
            'consecutive_days': r['consecutive_days'],
            'stage_chg_pct': r['stage_chg_pct'],
            'cumulative_turnover': r['cumulative_turnover'],
            'industry': r['industry'],
        })
    logger.info(f"加载 {len(strong_rank_cache)} 条强势股排名数据")

    # 加载机构预期数据
    cur.execute("""
        SELECT ts_code, forecast_year, institution_count, eps_mean,
               eps_min, eps_max, industry_avg
        FROM earnings_forecast WHERE forecast_year = EXTRACT(YEAR FROM CURRENT_DATE);
    """)
    earnings_cache = {}
    for r in cur.fetchall():
        earnings_cache[r['ts_code']] = {
            'forecast_year': r['forecast_year'],
            'institution_count': r['institution_count'],
            'eps_mean': float(r['eps_mean']) if r['eps_mean'] else None,
            'eps_min': float(r['eps_min']) if r['eps_min'] else None,
            'eps_max': float(r['eps_max']) if r['eps_max'] else None,
            'industry_avg': float(r['industry_avg']) if r['industry_avg'] else None,
        }
    logger.info(f"加载 {len(earnings_cache)} 条机构预期数据")

    # 加载概念板块数据
    cur.execute("""
        SELECT concept_code, concept_name, pct_chg, turnover_rate,
               lead_stock_code, lead_stock_name, stock_count
        FROM concept_board_quotes WHERE trade_date=%s;
    """, (latest_trade_date,))
    concept_cache = {}
    for r in cur.fetchall():
        concept_cache[r['concept_code']] = {
            'concept_name': r['concept_name'],
            'pct_chg': r['pct_chg'],
            'turnover_rate': r['turnover_rate'],
            'lead_stock_code': r['lead_stock_code'],
            'lead_stock_name': r['lead_stock_name'],
            'stock_count': r['stock_count'],
        }
    logger.info(f"加载 {len(concept_cache)} 条概念板块数据")

    # 加载概念成分股映射（反向查找股票所属概念）
    cur.execute("""
        SELECT ts_code, concept_code, concept_name
        FROM concept_membership;
    """)
    stock_concept_map = {}
    for r in cur.fetchall():
        ts = r['ts_code']
        if ts not in stock_concept_map:
            stock_concept_map[ts] = []
        stock_concept_map[ts].append({
            'concept_code': r['concept_code'],
            'concept_name': r['concept_name'],
        })
    logger.info(f"加载 {len(stock_concept_map)} 条概念成分股映射")

    # 加载PE/PB估值数据（从daily_quotes）
    cur.execute("""
        SELECT ts_code, pe_ratio, pb_ratio
        FROM daily_quotes WHERE trade_date=%s
          AND pe_ratio IS NOT NULL;
    """, (latest_trade_date,))
    valuation_cache = {}
    for r in cur.fetchall():
        valuation_cache[r['ts_code']] = {
            'pe_ratio': float(r['pe_ratio']) if r['pe_ratio'] else None,
            'pb_ratio': float(r['pb_ratio']) if r['pb_ratio'] else None,
        }
    logger.info(f"加载 {len(valuation_cache)} 条PE/PB估值数据")

    # 加载财务质量数据（最新季报）
    cur.execute("""
        SELECT DISTINCT ON (ts_code)
               ts_code, net_margin, gross_margin, debt_ratio,
               revenue, net_profit, operating_cashflow
        FROM stock_fundamentals
        ORDER BY ts_code, report_date DESC;
    """)
    fundamentals_cache = {}
    for r in cur.fetchall():
        fundamentals_cache[r['ts_code']] = {
            'net_margin': float(r['net_margin']) if r['net_margin'] else None,
            'gross_margin': float(r['gross_margin']) if r['gross_margin'] else None,
            'debt_ratio': float(r['debt_ratio']) if r['debt_ratio'] else None,
            'revenue': float(r['revenue']) if r['revenue'] else None,
            'net_profit': float(r['net_profit']) if r['net_profit'] else None,
            'operating_cashflow': float(r['operating_cashflow']) if r['operating_cashflow'] else None,
        }
    logger.info(f"加载 {len(fundamentals_cache)} 条财务质量数据")

    # 加载上市时间（用于次新股过滤）
    cur.execute("""
        SELECT ts_code, list_date
        FROM stock_basic_info
        WHERE list_date IS NOT NULL;
    """)
    list_date_cache = {}
    for r in cur.fetchall():
        list_date_cache[r['ts_code']] = r['list_date']
    logger.info(f"加载 {len(list_date_cache)} 条上市时间数据")

    cur.execute("""
        SELECT
          e.ts_code, MAX(e.stock_name) AS stock_name,
          COUNT(*) AS mention_count,
          COUNT(DISTINCT
            CASE
                WHEN e.source_name IN ('AKShare-龙虎榜', 'AKShare-涨停板') THEN 'capital'
                WHEN e.source_name IN ('AKShare-个股研报', 'AKShare-机构调研',
                                        '东财-盈利预测', 'THS-盈利预测') THEN 'research'
                WHEN e.source_name IN ('AKShare-财经新闻', 'AKShare-热点概念',
                                        '财联社-电报') THEN 'news'
                WHEN e.source_name IN ('THS-强势股', 'THS-概念标签',
                                        'MootDX-实时行情', 'Tencent-行情补充') THEN 'market'
                WHEN e.source_name IN ('巨潮-公告', 'MootDX-公告') THEN 'announcement'
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
            ts = r['ts_code']
            
            # 次新股过滤：上市<60天直接跳过（波动不可控）
            if ts in list_date_cache:
                list_date = list_date_cache[ts]
                if list_date and (today - list_date).days < 60:
                    logger.debug(f"{ts} 上市不足60天（次新股），跳过")
                    continue
            
            q = quote_cache.get(r['ts_code'])

            if not q or q.get('close') is None:
                logger.debug(f"{r['ts_code']} 无行情，跳过")
                continue

            close_price = float(q['close'])
            amount = float(q.get('amount') or 0)
            pct_chg = q.get('pct_chg') or 0
            turnover = q.get('turnover_rate') or 0
            amplitude = q.get('amplitude') or 0
            volume_ratio = q.get('volume_ratio') or 0
            commission_ratio = q.get('commission_ratio') or 0
            large_order_net = q.get('large_order_net') or 0
            main_force_net = q.get('main_force_net') or 0

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

            # 振幅评分：3%-8%为活跃区间，加分
            if 3 <= amplitude <= 8:
                quant_score += 10 * (1 - abs(amplitude - 5.5) / 2.5)
            elif amplitude > 8:
                quant_score += 5  # 振幅过大，适度加分但低于最佳区间

            # 量比评分：量比>1.5表示放量，加分
            if volume_ratio > 1.5:
                quant_score += min(15, (volume_ratio - 1.5) * 10)
            elif volume_ratio < 0.8:
                quant_score -= 5  # 缩量，减分

            # 委比评分：委比>0表示买盘强，加分
            if commission_ratio > 0:
                quant_score += min(10, commission_ratio / 10)
            elif commission_ratio < -20:
                quant_score -= 5  # 卖盘压力大

            # 大单净量评分：大单净量>0表示主力买入
            if large_order_net > 0:
                quant_score += min(15, large_order_net * 5)
            elif large_order_net < -5:
                quant_score -= 10  # 大单卖出，减分

            # 主力资金净流入评分
            if main_force_net > 0:
                quant_score += min(10, main_force_net / 1e6 * 2)  # 每百万加2分，最多10分
            elif main_force_net < -1e7:
                quant_score -= 5  # 主力大幅流出

            # 强势股加分
            if ts in strong_rank_cache:
                strong_ranks = strong_rank_cache[ts]
                # 取最高排名加分
                best_rank_bonus = 0
                for sr in strong_ranks:
                    pos = sr['rank_position'] or 999
                    if pos <= 10:
                        bonus = 20
                    elif pos <= 30:
                        bonus = 10
                    elif pos <= 50:
                        bonus = 5
                    else:
                        bonus = 0
                    best_rank_bonus = max(best_rank_bonus, bonus)
                
                # 连续上涨天数加分
                consecutive_days = max((sr['consecutive_days'] or 0) for sr in strong_ranks)
                if consecutive_days >= 5:
                    best_rank_bonus += 10
                elif consecutive_days >= 3:
                    best_rank_bonus += 5
                
                quant_score += best_rank_bonus

            # 机构预期加分
            if ts in earnings_cache:
                fc = earnings_cache[ts]
                inst_count = fc['institution_count'] or 0
                if inst_count >= 10:
                    quant_score += 15  # 多家机构覆盖
                elif inst_count >= 5:
                    quant_score += 8
                
                # EPS高于行业平均加分
                if fc['eps_mean'] and fc['industry_avg'] and fc['industry_avg'] > 0:
                    eps_premium = (fc['eps_mean'] - fc['industry_avg']) / fc['industry_avg']
                    if eps_premium > 0.1:  # 高于行业10%
                        quant_score += 10
                    elif eps_premium > 0.05:
                        quant_score += 5

            # 概念板块加分
            if ts in stock_concept_map:
                concepts = stock_concept_map[ts]
                best_concept_bonus = 0
                for c in concepts:
                    concept_data = concept_cache.get(c['concept_code'])
                    if concept_data:
                        concept_pct = concept_data['pct_chg'] or 0
                        if concept_pct > 3:
                            bonus = 10  # 热门概念
                        elif concept_pct > 1:
                            bonus = 5
                        else:
                            bonus = 0
                        best_concept_bonus = max(best_concept_bonus, bonus)
                quant_score += best_concept_bonus

            # PE/PB估值评分：低估值加分，高估值扣分
            if ts in valuation_cache:
                val = valuation_cache[ts]
                pe = val['pe_ratio']
                pb = val['pb_ratio']
                
                # PE估值评分
                if pe is not None:
                    if pe <= 0:
                        quant_score -= 20  # 亏损公司，大幅扣分
                    elif pe < 15:
                        quant_score += 15  # 低估值，加分
                    elif pe < 30:
                        quant_score += 5   # 合理估值，小幅加分
                    elif pe < 60:
                        pass               # 中等估值，不加分不减分
                    elif pe < 100:
                        quant_score -= 10  # 高估值，扣分
                    else:
                        quant_score -= 20  # 极高估值，大幅扣分

                # PB估值评分
                if pb is not None:
                    if pb <= 0:
                        quant_score -= 10  # 负PB（资不抵债），扣分
                    elif pb < 2:
                        quant_score += 10  # 低PB，加分
                    elif pb < 5:
                        quant_score += 3   # 合理PB，小幅加分
                    elif pb < 10:
                        pass               # 中等PB
                    else:
                        quant_score -= 10  # 高PB，扣分

            # 财务质量评分
            if ts in fundamentals_cache:
                fin = fundamentals_cache[ts]
                
                # 净利润率评分
                net_margin = fin['net_margin']
                if net_margin is not None:
                    if net_margin > 20:
                        quant_score += 10  # 高利润率，加分
                    elif net_margin > 10:
                        quant_score += 5
                    elif net_margin < 0:
                        quant_score -= 10  # 亏损，扣分
                
                # 毛利率评分
                gross_margin = fin['gross_margin']
                if gross_margin is not None:
                    if gross_margin > 40:
                        quant_score += 8   # 高毛利率，加分
                    elif gross_margin > 20:
                        quant_score += 3
                    elif gross_margin < 10:
                        quant_score -= 5   # 低毛利率，扣分
                
                # 资产负债率评分
                debt_ratio = fin['debt_ratio']
                if debt_ratio is not None:
                    if debt_ratio < 40:
                        quant_score += 5   # 低负债，加分
                    elif debt_ratio > 70:
                        quant_score -= 10  # 高负债，扣分
                
                # 经营现金流评分
                op_cashflow = fin['operating_cashflow']
                net_profit = fin['net_profit']
                if op_cashflow is not None and net_profit is not None and net_profit > 0:
                    # 经营现金流/净利润 > 1 表示盈利质量高
                    cashflow_ratio = op_cashflow / net_profit
                    if cashflow_ratio > 1.2:
                        quant_score += 8   # 盈利质量高，加分
                    elif cashflow_ratio < 0.5:
                        quant_score -= 5   # 盈利质量低，扣分

            consensus = min(r['source_diversity'] / 2.0, 1.0)
            llm_n = min(r['llm_score'] / 5.0, 1.0)
            # 提及次数加成
            mention_bonus = min(0.2, r['mention_count'] * 0.03)
            llm_n = min(1.0, llm_n + mention_bonus)
            quant_score = max(0, quant_score)  # 防止负分
            quant_n = min(quant_score / 100.0, 1.0)  # 上限1.0

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
                'consensus_score': consensus * 100, 'llm_score': llm_n * 100,
                'quant_score': quant_score, 'final_score': final,
                'logic_tags': r['logic_tags'], 'sources': r['sources'],
                'close': close_price,
                'pct_chg': pct_chg,
                'turnover_rate': turnover,
            })
    else:
        logger.info("无 LLM 推荐数据，使用纯量化选股")
        cur.execute("""
            SELECT ts_code, close, pct_chg, turnover_rate, amount, volume,
                   amplitude, volume_ratio, commission_ratio, large_order_net, main_force_net
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

            # 次新股过滤：上市<60天直接跳过（波动不可控）
            if ts_code in list_date_cache:
                list_date = list_date_cache[ts_code]
                if list_date and (today - list_date).days < 60:
                    continue

            close_price = float(q['close']) if q['close'] is not None else None
            pct_chg = q['pct_chg'] or 0
            turnover = q['turnover_rate'] or 0
            amount = q['amount'] or 0
            amplitude = q['amplitude'] or 0
            volume_ratio = q['volume_ratio'] or 0
            commission_ratio = q['commission_ratio'] or 0
            large_order_net = q['large_order_net'] or 0
            main_force_net = q['main_force_net'] or 0

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

            # 振幅评分：3%-8%为活跃区间，加分
            if 3 <= amplitude <= 8:
                quant_score += 10 * (1 - abs(amplitude - 5.5) / 2.5)
            elif amplitude > 8:
                quant_score += 5

            # 量比评分：量比>1.5表示放量，加分
            if volume_ratio > 1.5:
                quant_score += min(15, (volume_ratio - 1.5) * 10)
            elif volume_ratio < 0.8:
                quant_score -= 5

            # 委比评分：委比>0表示买盘强，加分
            if commission_ratio > 0:
                quant_score += min(10, commission_ratio / 10)
            elif commission_ratio < -20:
                quant_score -= 5

            # 大单净量评分：大单净量>0表示主力买入
            if large_order_net > 0:
                quant_score += min(15, large_order_net * 5)
            elif large_order_net < -5:
                quant_score -= 10

            # 主力资金净流入评分
            if main_force_net > 0:
                quant_score += min(10, main_force_net / 1e6 * 2)
            elif main_force_net < -1e7:
                quant_score -= 5

            # 强势股加分
            if ts_code in strong_rank_cache:
                strong_ranks = strong_rank_cache[ts_code]
                best_rank_bonus = 0
                for sr in strong_ranks:
                    pos = sr['rank_position'] or 999
                    if pos <= 10:
                        bonus = 20
                    elif pos <= 30:
                        bonus = 10
                    elif pos <= 50:
                        bonus = 5
                    else:
                        bonus = 0
                    best_rank_bonus = max(best_rank_bonus, bonus)
                
                consecutive_days = max((sr['consecutive_days'] or 0) for sr in strong_ranks)
                if consecutive_days >= 5:
                    best_rank_bonus += 10
                elif consecutive_days >= 3:
                    best_rank_bonus += 5
                
                quant_score += best_rank_bonus

            # 机构预期加分
            if ts_code in earnings_cache:
                fc = earnings_cache[ts_code]
                inst_count = fc['institution_count'] or 0
                if inst_count >= 10:
                    quant_score += 15
                elif inst_count >= 5:
                    quant_score += 8
                
                if fc['eps_mean'] and fc['industry_avg'] and fc['industry_avg'] > 0:
                    eps_premium = (fc['eps_mean'] - fc['industry_avg']) / fc['industry_avg']
                    if eps_premium > 0.1:
                        quant_score += 10
                    elif eps_premium > 0.05:
                        quant_score += 5

            # 概念板块加分
            if ts_code in stock_concept_map:
                concepts = stock_concept_map[ts_code]
                best_concept_bonus = 0
                for c in concepts:
                    concept_data = concept_cache.get(c['concept_code'])
                    if concept_data:
                        concept_pct = concept_data['pct_chg'] or 0
                        if concept_pct > 3:
                            bonus = 10
                        elif concept_pct > 1:
                            bonus = 5
                        else:
                            bonus = 0
                        best_concept_bonus = max(best_concept_bonus, bonus)
                quant_score += best_concept_bonus

            # PE/PB估值评分：低估值加分，高估值扣分
            if ts_code in valuation_cache:
                val = valuation_cache[ts_code]
                pe = val['pe_ratio']
                pb = val['pb_ratio']
                
                if pe is not None:
                    if pe <= 0:
                        quant_score -= 20
                    elif pe < 15:
                        quant_score += 15
                    elif pe < 30:
                        quant_score += 5
                    elif pe < 60:
                        pass
                    elif pe < 100:
                        quant_score -= 10
                    else:
                        quant_score -= 20

                if pb is not None:
                    if pb <= 0:
                        quant_score -= 10
                    elif pb < 2:
                        quant_score += 10
                    elif pb < 5:
                        quant_score += 3
                    elif pb < 10:
                        pass
                    else:
                        quant_score -= 10

            # 财务质量评分
            if ts_code in fundamentals_cache:
                fin = fundamentals_cache[ts_code]
                
                net_margin = fin['net_margin']
                if net_margin is not None:
                    if net_margin > 20:
                        quant_score += 10
                    elif net_margin > 10:
                        quant_score += 5
                    elif net_margin < 0:
                        quant_score -= 10
                
                gross_margin = fin['gross_margin']
                if gross_margin is not None:
                    if gross_margin > 40:
                        quant_score += 8
                    elif gross_margin > 20:
                        quant_score += 3
                    elif gross_margin < 10:
                        quant_score -= 5
                
                debt_ratio = fin['debt_ratio']
                if debt_ratio is not None:
                    if debt_ratio < 40:
                        quant_score += 5
                    elif debt_ratio > 70:
                        quant_score -= 10
                
                op_cashflow = fin['operating_cashflow']
                net_profit = fin['net_profit']
                if op_cashflow is not None and net_profit is not None and net_profit > 0:
                    cashflow_ratio = op_cashflow / net_profit
                    if cashflow_ratio > 1.2:
                        quant_score += 8
                    elif cashflow_ratio < 0.5:
                        quant_score -= 5

            final = quant_score * 0.8

            candidates.append({
                'ts_code': ts_code, 'stock_name': name_cache.get(ts_code, ''),
                'mention_count': 1, 'source_diversity': 1,
                'consensus_score': 30.0, 'llm_score': 0,
                'quant_score': quant_score, 'final_score': final,
                'logic_tags': ['量化选股'], 'sources': [{'source': 'quant', 'tier': 2}],
                'close': close_price,
                'pct_chg': pct_chg,
                'turnover_rate': turnover,
            })

    # 按 ts_code 去重，保留分数最高的版本
    seen = {}
    unique_candidates = []
    for c in candidates:
        ts = c['ts_code']
        if ts not in seen or c['final_score'] > seen[ts]['final_score']:
            seen[ts] = c
    unique_candidates = list(seen.values())
    
    unique_candidates.sort(key=lambda x: x['final_score'], reverse=True)
    candidates = unique_candidates
    logger.info(f"去重排序后候选池: {len(candidates)} 只")
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
        # 仓位随共识度缩放：共识100→8%，共识50→4%
        c['position_pct'] = round(0.08 * c.get('consensus_score', 50) / 100, 4) if is_selected else 0
        if levels:
            c.update(levels)
        items.append(c)

    n = write_candidates(items, today, source=SOURCE, run_mode=RUN_MODE, conn=conn)
    _persist_llm_scan_stats(today, len(candidates), len(qualified), len(selected_list), conn)
    cur.close(); conn.close()
    logger.info(f"候选池生成完毕，snapshot_date={today}，共写入 {n} 条记录")


def _persist_llm_scan_stats(snapshot_date, total_candidates, total_qualified, total_selected, conn):
    """将 LLM 多源聚合统计写入 strategy_scans，供 HTML 报告渲染"""
    try:
        cur = conn.cursor()
        filter_stats = {
            "多源聚合后": total_candidates,
            "综合评分≥阈值": total_qualified,
            "精选标记": total_selected,
        }
        cur.execute("""
            INSERT INTO strategy_scans
                (snapshot_date, strategy, total_scanned, total_passed, filter_stats)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (snapshot_date, strategy) DO UPDATE SET
                total_scanned = EXCLUDED.total_scanned,
                total_passed  = EXCLUDED.total_passed,
                filter_stats  = EXCLUDED.filter_stats;
        """, (
            snapshot_date, SOURCE, total_candidates, total_selected,
            json.dumps(filter_stats, ensure_ascii=False),
        ))
        cur.close()
    except Exception as e:
        logger.warning(f"扫描统计写入失败: {e}")


if __name__ == '__main__':
    aggregate_today()
