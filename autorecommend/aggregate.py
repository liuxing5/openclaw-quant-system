"""每日候选池生成 - 盘前/盘后双跑"""
import os
import json
from datetime import date, timedelta, datetime, timezone
import psycopg2
from psycopg2.extras import RealDictCursor
from loguru import logger
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

RUN_MODE = os.getenv('RUN_MODE', 'morning')
MIN_SELECT_SCORE = 50
MAX_SELECTED = 8
MIN_LIQUIDITY = 1e8

# 北京时间时区
BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date():
    """获取北京时间日期（解决 GitHub Actions UTC 时区问题）"""
    return datetime.now(BEIJING_TZ).date()


def get_db():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST'),
        port=int(os.getenv('POSTGRES_PORT') or '5432'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        dbname=os.getenv('POSTGRES_DB'),
    )


def aggregate_today():
    today = get_beijing_date()
    cutoff = today - timedelta(days=2)

    conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)

    # 自动迁移：检查并添加 run_mode 列
    try:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'daily_candidates' AND column_name = 'run_mode';
        """)
        if not cur.fetchone():
            logger.info("自动迁移：添加 run_mode 列")
            cur.execute("""
                ALTER TABLE daily_candidates ADD COLUMN run_mode VARCHAR(20) DEFAULT 'afternoon';
            """)
            conn.commit()
            logger.info("run_mode 列添加成功")
        
        # 检查并添加唯一约束
        cur.execute("""
            SELECT conname FROM pg_constraint
            WHERE conname = 'daily_candidates_unique_mode';
        """)
        if not cur.fetchone():
            logger.info("自动迁移：添加唯一约束")
            cur.execute("""
                ALTER TABLE daily_candidates ADD CONSTRAINT daily_candidates_unique_mode
                UNIQUE (snapshot_date, ts_code, run_mode);
            """)
            conn.commit()
            logger.info("唯一约束添加成功")
    except Exception as e:
        logger.warning(f"迁移检查失败: {e}")

    # 批量加载今日行情到内存，避免逐个查询
    cur.execute("""
        SELECT ts_code, close, pct_chg, turnover_rate, amount
        FROM daily_quotes WHERE trade_date=%s;
    """, (today,))
    quote_cache = {}
    for q in cur.fetchall():
        quote_cache[q['ts_code']] = {
            'close': float(q['close']) if q['close'] else None,
            'pct_chg': float(q['pct_chg']) if q['pct_chg'] else 0,
            'turnover_rate': float(q['turnover_rate']) if q['turnover_rate'] else 0,
            'amount': float(q['amount']) if q['amount'] else 0,
        }
    logger.info(f"加载 {len(quote_cache)} 条行情到缓存")

    cur.execute("""
        SELECT
          e.ts_code, MAX(e.stock_name) AS stock_name,
          COUNT(*) AS mention_count,
          COUNT(DISTINCT e.source_name) AS source_diversity,
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
            q = quote_cache.get(r['ts_code'])

            if not q or not q.get('close'):
                logger.debug(f"{r['ts_code']} 无行情，跳过")
                continue

            close_price = float(q['close'])
            amount = float(q.get('amount') or 0)

            if amount < MIN_LIQUIDITY:
                logger.debug(f"{r['ts_code']} 流动性不足 {amount/1e8:.2f}亿")
                continue

            quant_score = 0
            pct_chg = q.get('pct_chg') or 0
            turnover = q.get('turnover_rate') or 0

            ts = r['ts_code']
            is_kc_cy = ts.split('.')[0].startswith(('688', '300', '301'))
            limit_threshold = 19.5 if is_kc_cy else 9.5

            if RUN_MODE == 'afternoon' and pct_chg >= limit_threshold:
                logger.info(f"{ts} 今日已涨停 {pct_chg:.1f}%，盘后模式跳过（次日陷阱风险）")
                continue

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

            has_lhb = any('龙虎榜' in (s.get('source') or '') for s in (r['sources'] or []))
            has_zt = any('涨停' in (s.get('source') or '') for s in (r['sources'] or []))
            if has_lhb and has_zt:
                if RUN_MODE == 'morning':
                    logger.info(f"{r['ts_code']} 龙虎榜+涨停板共振，盘前模式直接过滤（T+1陷阱风险）")
                    continue
                final = final * 0.6
                logger.debug(f"{r['ts_code']} 龙虎榜+涨停板共振，降权 40%（T+1陷阱风险）")

            candidates.append({
                'ts_code': r['ts_code'], 'stock_name': r['stock_name'],
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
            cur.execute("""
                INSERT INTO daily_candidates
                (snapshot_date, ts_code, stock_name, mention_count, source_diversity,
                 consensus_score, llm_score, quant_score, final_score, logic_tags,
                 selected, position_pct, sources, run_mode)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,0,%s,%s)
                ON CONFLICT (snapshot_date, ts_code, run_mode) DO UPDATE SET
                  final_score=EXCLUDED.final_score, selected=EXCLUDED.selected;
            """, (today, c['ts_code'], c['stock_name'], c['mention_count'],
                  c['source_diversity'], c['consensus_score'], c['llm_score'],
                  c['quant_score'], c['final_score'], c['logic_tags'],
                  json.dumps(c['sources'], default=str, ensure_ascii=False),
                  RUN_MODE))
        conn.commit()
        cur.close(); conn.close()
        return

    selected_list = qualified[:MAX_SELECTED]
    logger.info(f"合格 {len(qualified)} 只，选中 {len(selected_list)} 只")

    for i, c in enumerate(qualified[:15]):
        is_selected = c in selected_list
        close = c['close']
        code = c['ts_code'].split('.')[0]

        if code.startswith(('688', '300', '301')):
            entry_low = round(close * 0.985, 2)
            entry_high = round(close * 1.015, 2)
            stop = round(close * 0.95, 2)
            t1 = round(close * 1.08, 2)
            t2 = round(close * 1.15, 2)
        else:
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
             selected, position_pct, entry_low, entry_high, stop_loss, target_1, target_2, sources, run_mode)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (snapshot_date, ts_code, run_mode) DO UPDATE SET
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
            RUN_MODE,
        ))

    conn.commit()
    cur.close(); conn.close()
    logger.info(f"候选池生成完毕")


if __name__ == '__main__':
    aggregate_today()
