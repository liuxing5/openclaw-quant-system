"""每日候选池生成 - 跑在 15:30 收盘后"""
import os
import json
from datetime import date, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

MIN_SELECT_SCORE = 50
MAX_SELECTED = 8
MIN_LIQUIDITY = 1e8


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
    )


def get_or_fetch_quote(cur, ts_code, today):
    """优先查库，没有则实时拉一次"""
    cur.execute("""
        SELECT close, pct_chg, turnover_rate, amount
        FROM daily_quotes WHERE ts_code=%s AND trade_date=%s;
    """, (ts_code, today))
    q = cur.fetchone()
    if q:
        return dict(q) if hasattr(q, 'keys') else {
            'close': q[0], 'pct_chg': q[1],
            'turnover_rate': q[2], 'amount': q[3]
        }

    try:
        import akshare as ak
        code_pure = ts_code.split('.')[0]
        df = ak.stock_zh_a_spot_em()
        match = df[df['代码'] == code_pure]
        if not match.empty:
            r = match.iloc[0]
            cur.execute("""
                INSERT INTO daily_quotes
                (ts_code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover_rate)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ts_code, trade_date) DO UPDATE SET close=EXCLUDED.close;
            """, (ts_code, today, r['今开'], r['最高'], r['最低'], r['最新价'],
                  int(r['成交量']) if r.get('成交量') else None,
                  r.get('成交额'), r.get('涨跌幅'), r.get('换手率')))
            return {
                'close': r['最新价'], 'pct_chg': r['涨跌幅'],
                'turnover_rate': r['换手率'], 'amount': r['成交额']
            }
    except Exception as e:
        logger.warning(f"实时拉 {ts_code} 失败: {e}")
    return None


def aggregate_today():
    today = date.today()
    cutoff = today - timedelta(days=2)

    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT
          e.ts_code, MAX(e.stock_name) AS stock_name,
          COUNT(*) AS mention_count,
          COUNT(DISTINCT e.source_name) AS source_diversity,
          AVG(e.strength * COALESCE(e.confidence,0.5)) AS llm_score,
          ARRAY_AGG(DISTINCT e.logic_category) AS logic_tags,
          JSON_AGG(JSON_BUILD_OBJECT(
            'source', e.source_name, 'tier', 2, 'strength', e.strength,
            'logic', e.logic_summary, 'pub_time', e.pub_time
          )) AS sources
        FROM extracted_recommendations e
        WHERE e.pub_time >= %s
          AND e.recommendation_type IN ('buy','strong_buy','watch')
          AND e.strength >= 2
        GROUP BY e.ts_code
        HAVING COUNT(*) >= 1;
    """, (cutoff,))
    rows = cur.fetchall()

    candidates = []

    if rows:
        logger.info(f"从 LLM 提取到 {len(rows)} 只推荐")
        rows = [r for r in rows if r['ts_code'].endswith(('.SH', '.SZ'))]
        rows = [r for r in rows if not (r['stock_name'] and 'ST' in r['stock_name'].upper())]

        for r in rows:
            q = get_or_fetch_quote(cur, r['ts_code'], today)

            if not q or not q.get('close'):
                logger.warning(f"{r['ts_code']} 无行情，跳过")
                continue

            close_price = float(q['close'])
            amount = q.get('amount') or 0

            if amount < MIN_LIQUIDITY:
                logger.debug(f"{r['ts_code']} 流动性不足 {amount/1e8:.2f}亿")
                continue

            quant_score = 0
            pct_chg = q.get('pct_chg') or 0
            turnover = q.get('turnover_rate') or 0

            if -3 < pct_chg < 7:
                quant_score += 30
            if turnover > 3:
                quant_score += 20
            if amount > 5e8:
                quant_score += 30
            elif amount > 2e8:
                quant_score += 20
            if pct_chg > 9.5:
                quant_score = max(0, quant_score - 30)

            consensus = min(r['source_diversity'] / 3.0, 1.0)
            llm_n = min(r['llm_score'] / 5.0, 1.0)
            quant_n = quant_score / 100.0

            if llm_n > 0 and quant_n > 0:
                final = (llm_n ** 0.4) * (quant_n ** 0.6) * (0.5 + 0.5 * consensus) * 100
            else:
                final = 0

            candidates.append({
                'ts_code': r['ts_code'], 'stock_name': r['stock_name'],
                'mention_count': r['mention_count'], 'source_diversity': r['source_diversity'],
                'consensus_score': consensus, 'llm_score': llm_n * 100,
                'quant_score': quant_score, 'final_score': final,
                'logic_tags': r['logic_tags'], 'sources': r['sources'],
                'close': close_price,
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
        """, (today,))
        quotes = cur.fetchall()

        for q in quotes:
            ts_code = q['ts_code']
            if not (ts_code.endswith('.SH') or ts_code.endswith('.SZ')):
                continue

            close_price = float(q['close']) if q['close'] else None
            pct_chg = q['pct_chg'] or 0
            turnover = q['turnover_rate'] or 0
            amount = q['amount'] or 0

            quant_score = 0
            if -2 < pct_chg < 5:
                quant_score += 40
            if turnover > 5:
                quant_score += 30
            elif turnover > 3:
                quant_score += 20
            if amount > 10e8:
                quant_score += 30
            elif amount > 5e8:
                quant_score += 20

            final = quant_score * 0.8

            candidates.append({
                'ts_code': ts_code, 'stock_name': None,
                'mention_count': 1, 'source_diversity': 1,
                'consensus_score': 0.3, 'llm_score': 0,
                'quant_score': quant_score, 'final_score': final,
                'logic_tags': ['量化选股'], 'sources': [{'source': 'quant', 'tier': 2}],
                'close': close_price,
            })

    candidates.sort(key=lambda x: x['final_score'], reverse=True)

    qualified = [c for c in candidates if c['final_score'] >= MIN_SELECT_SCORE]

    if not qualified:
        logger.warning(f"无候选股达到阈值 {MIN_SELECT_SCORE}")
        observation = candidates[:10]
        for c in observation:
            cur.execute("""
                INSERT INTO daily_candidates
                (snapshot_date, ts_code, stock_name, mention_count, source_diversity,
                 consensus_score, llm_score, quant_score, final_score, logic_tags,
                 selected, position_pct, sources)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,0,%s)
                ON CONFLICT (snapshot_date, ts_code) DO UPDATE SET
                  final_score=EXCLUDED.final_score, selected=EXCLUDED.selected;
            """, (today, c['ts_code'], c['stock_name'], c['mention_count'],
                  c['source_diversity'], c['consensus_score'], c['llm_score'],
                  c['quant_score'], c['final_score'], c['logic_tags'],
                  json.dumps(c['sources'], default=str, ensure_ascii=False)))
        conn.commit()
        cur.close(); conn.close()
        return

    selected_list = qualified[:MAX_SELECTED]
    logger.info(f"合格 {len(qualified)} 只，选中 {len(selected_list)} 只")

    for i, c in enumerate(qualified[:15]):
        is_selected = c in selected_list
        close = c['close']
        entry_low = round(close * 0.99, 2)
        entry_high = round(close * 1.01, 2)
        stop = round(close * 0.97, 2)
        t1 = round(close * 1.05, 2)
        t2 = round(close * 1.10, 2)
        position = 0.08 if is_selected else 0

        cur.execute("""
            INSERT INTO daily_candidates
            (snapshot_date, ts_code, stock_name, mention_count, source_diversity,
             consensus_score, llm_score, quant_score, final_score, logic_tags,
             selected, position_pct, entry_low, entry_high, stop_loss, target_1, target_2, sources)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (snapshot_date, ts_code) DO UPDATE SET
              final_score=EXCLUDED.final_score, selected=EXCLUDED.selected,
              entry_low=EXCLUDED.entry_low, entry_high=EXCLUDED.entry_high,
              stop_loss=EXCLUDED.stop_loss, target_1=EXCLUDED.target_1,
              target_2=EXCLUDED.target_2, position_pct=EXCLUDED.position_pct;
        """, (
            today, c['ts_code'], c['stock_name'], c['mention_count'],
            c['source_diversity'], c['consensus_score'], c['llm_score'],
            c['quant_score'], c['final_score'], c['logic_tags'],
            is_selected, position, entry_low, entry_high, stop, t1, t2,
            json.dumps(c['sources'], default=str, ensure_ascii=False),
        ))

    conn.commit()
    cur.close(); conn.close()
    logger.info(f"候选池生成完毕")


if __name__ == '__main__':
    aggregate_today()
