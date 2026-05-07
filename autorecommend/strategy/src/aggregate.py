"""每日候选池生成 - 跑在 15:30 收盘后"""
import os
import json
from datetime import date, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(BASE_DIR, '.env'))


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'), user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'), dbname=os.getenv('POSTGRES_DB'),
    )


def aggregate_today():
    today = date.today()
    cutoff = today - timedelta(days=2)
    
    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT 
          e.ts_code, MAX(e.stock_name) AS stock_name, 
          COUNT(*) AS mention_count, 
          COUNT(DISTINCT e.source_id) AS source_diversity, 
          AVG(e.strength * COALESCE(s.weight,1) * COALESCE(e.confidence,0.5)) AS llm_score, 
          ARRAY_AGG(DISTINCT e.logic_category) AS logic_tags, 
          JSON_AGG(JSON_BUILD_OBJECT( 
            'source', s.name, 'tier', s.tier, 'strength', e.strength, 
            'logic', e.logic_summary, 'pub_time', e.pub_time 
          )) AS sources 
        FROM extracted_recommendations e 
        JOIN feed_sources s ON e.source_id=s.id 
        WHERE e.pub_time >= %s 
          AND e.recommendation_type IN ('buy','strong_buy','watch') 
          AND e.strength >= 2 
        GROUP BY e.ts_code 
        HAVING COUNT(*) >= 1;
    """, (cutoff,))
    rows = cur.fetchall()
    
    # 只保留 A 股
    rows = [r for r in rows if r['ts_code'].endswith('.SH') or r['ts_code'].endswith('.SZ')]
    rows = [r for r in rows if not (r['stock_name'] and 'ST' in r['stock_name'].upper())]
    
    candidates = []
    for r in rows:
        cur.execute("""
            SELECT close, pct_chg, turnover_rate, amount 
            FROM daily_quotes WHERE ts_code=%s AND trade_date=%s;
        """, (r['ts_code'], today))
        q = cur.fetchone()
        
        quant_score = 0
        close_price = None
        has_market_data = False
        
        if q:
            has_market_data = True
            close_price = float(q['close']) if q['close'] else None
            if (q['amount'] or 0) < 1e8:
                continue
            if q['pct_chg'] and -3 < q['pct_chg'] < 7:
                quant_score += 30
            if (q['turnover_rate'] or 0) > 3:
                quant_score += 20
            if (q['amount'] or 0) > 5e8:
                quant_score += 20
        else:
            quant_score = 50
            logger.warning(f"{r['ts_code']} 无行情数据，使用默认量化分")
        
        consensus = min(r['source_diversity'] / 3.0, 1.0)
        llm_n = min(r['llm_score'] / 5.0, 1.0)
        quant_n = quant_score / 100.0
        final = (llm_n ** 0.4) * (quant_n ** 0.6) * (0.5 + 0.5 * consensus) * 100
        
        candidates.append({
            'ts_code': r['ts_code'], 'stock_name': r['stock_name'],
            'mention_count': r['mention_count'], 'source_diversity': r['source_diversity'],
            'consensus_score': consensus, 'llm_score': llm_n * 100,
            'quant_score': quant_score, 'final_score': final,
            'logic_tags': r['logic_tags'], 'sources': r['sources'],
            'close': close_price,
        })
    
    candidates.sort(key=lambda x: x['final_score'], reverse=True)
    top_n = candidates[:15]
    
    for i, c in enumerate(top_n):
        selected = i < 5
        entry_low = round(c['close'] * 0.99, 2) if c['close'] else None
        entry_high = round(c['close'] * 1.01, 2) if c['close'] else None
        stop = round(c['close'] * 0.98, 2) if c['close'] else None
        t1 = round(c['close'] * 1.05, 2) if c['close'] else None
        t2 = round(c['close'] * 1.10, 2) if c['close'] else None
        position = 0.08 if selected else 0
        
        cur.execute("""
            INSERT INTO daily_candidates
            (snapshot_date, ts_code, stock_name, mention_count, source_diversity,
             consensus_score, llm_score, quant_score, final_score, logic_tags,
             selected, position_pct, entry_low, entry_high, stop_loss, target_1, target_2, sources)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (snapshot_date, ts_code) DO UPDATE SET
              final_score=EXCLUDED.final_score, selected=EXCLUDED.selected;
        """, (
            today, c['ts_code'], c['stock_name'], c['mention_count'],
            c['source_diversity'], c['consensus_score'], c['llm_score'],
            c['quant_score'], c['final_score'], c['logic_tags'],
            selected, position, entry_low, entry_high, stop, t1, t2,
            json.dumps(c['sources'], default=str, ensure_ascii=False),
        ))
    
    conn.commit()
    cur.close(); conn.close()
    logger.info(f"Generated {len(top_n)} candidates, {min(5, len(top_n))} selected")


if __name__ == '__main__':
    aggregate_today()
