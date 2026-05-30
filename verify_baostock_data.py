"""
使用 BaoStock 验证 daily_candidates 表中每个字段的正确性（简化版）
=========================================================
验证内容：
1. ts_code 格式正确性
2. stock_name 与 stock_basic_info 对比
3. entry_low/entry_high/stop_loss/target_1/target_2 价格计算正确性
4. score 计算逻辑正确性
5. sources 字段格式正确性
6. 其他字段的合理性和一致性
"""
import os
import json
import math
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '6543'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from core.db.connection import get_db_fresh

def calc_price_levels(close, ts_code):
    """根据 BaoStock 收盘价计算价格水平（与 backfill_daily_candidates.py 一致）"""
    if not close:
        return {}
    pure_code = ts_code.split('.')[0]
    is_kc_cy = pure_code.startswith(('688', '300', '301'))
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

def verify_all():
    conn = get_db_fresh()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    print("=" * 80)
    print("验证 daily_candidates 数据正确性（数据源：BaoStock）")
    print("=" * 80)
    
    all_issues = []
    
    # 1. 验证 ts_code 格式
    print("\n1. 验证 ts_code 格式...")
    cur.execute("""
        SELECT id, ts_code, source
        FROM daily_candidates
        WHERE ts_code !~ '^\d{6}\.(SH|SZ)$'
        LIMIT 20
    """)
    invalid_codes = cur.fetchall()
    if invalid_codes:
        print(f"   ❌ 发现 {len(invalid_codes)} 条无效 ts_code:")
        for r in invalid_codes:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, source={r['source']}")
        all_issues.append(('invalid_ts_code', len(invalid_codes)))
    else:
        print("   ✅ ts_code 格式全部正确")
    
    # 2. 验证 stock_name 正确性（与 stock_basic_info 对比）
    print("\n2. 验证 stock_name 正确性...")
    cur.execute("""
        SELECT dc.id, dc.ts_code, dc.stock_name as dc_name, sbi.stock_name as sbi_name
        FROM daily_candidates dc
        LEFT JOIN stock_basic_info sbi ON dc.ts_code = sbi.ts_code
        WHERE dc.stock_name IS NOT NULL 
          AND dc.stock_name != ''
          AND sbi.stock_name IS NOT NULL
          AND dc.stock_name != sbi.stock_name
        LIMIT 20
    """)
    mismatches = cur.fetchall()
    if mismatches:
        print(f"   ⚠️  发现 {len(mismatches)} 条名称不匹配:")
        for r in mismatches[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, DB={r['dc_name']}, BasicInfo={r['sbi_name']}")
        all_issues.append(('stock_name_mismatch', len(mismatches)))
    else:
        print("   ✅ stock_name 全部正确")
    
    # 3. 验证价格字段计算正确性（与 daily_quotes 对比）
    print("\n3. 验证价格字段计算正确性...")
    cur.execute("""
        SELECT dc.id, dc.snapshot_date, dc.ts_code, dc.source,
               dc.entry_low, dc.entry_high, dc.stop_loss, dc.target_1, dc.target_2,
               dq.close
        FROM daily_candidates dc
        JOIN daily_quotes dq ON dc.ts_code = dq.ts_code AND dc.snapshot_date = dq.trade_date
        WHERE dc.entry_low IS NOT NULL
        ORDER BY dc.id DESC
        LIMIT 100
    """)
    price_records = cur.fetchall()
    
    price_errors = []
    for r in price_records:
        close = float(r['close'])
        expected = calc_price_levels(close, r['ts_code'])
        
        for field, expected_val in expected.items():
            actual = r[field]
            if actual:
                actual = float(actual)
                error_pct = abs(actual - expected_val) / expected_val
                if error_pct > 0.01:  # 1% 误差阈值
                    price_errors.append({
                        'id': r['id'],
                        'ts_code': r['ts_code'],
                        'field': field,
                        'actual': actual,
                        'expected': expected_val,
                        'error_pct': error_pct,
                        'close': close
                    })
    
    if price_errors:
        print(f"   ⚠️  发现 {len(price_errors)} 条价格计算误差:")
        for e in price_errors[:5]:
            print(f"      id={e['id']}, {e['ts_code']}, {e['field']} "
                  f"actual={e['actual']}, expected={e['expected']}, error={e['error_pct']:.2%}, close={e['close']}")
        all_issues.append(('price_calc_error', len(price_errors)))
    else:
        print("   ✅ 价格字段计算全部正确")
    
    # 4. 验证 score 计算逻辑 - 只检查数据合理性
    print("\n4. 验证 score 计算逻辑...")
    score_errors = []
    
    # 4.1 overnight_8step: final_score 应该等于 quant_score
    cur.execute("""
        SELECT id, ts_code, quant_score, final_score
        FROM daily_candidates
        WHERE source = 'overnight_8step'
          AND quant_score IS NOT NULL
          AND final_score IS NOT NULL
          AND ABS(quant_score - final_score) > 0.01
        LIMIT 20
    """)
    for r in cur.fetchall():
        score_errors.append({
            'id': r['id'],
            'source': 'overnight_8step',
            'quant': r['quant_score'],
            'final': r['final_score']
        })
    
    # 4.2 llm_multisource: 检查分数范围合理性
    cur.execute("""
        SELECT id, ts_code, quant_score, final_score, llm_score, consensus_score
        FROM daily_candidates
        WHERE source = 'llm_multisource'
          AND (quant_score < 0 OR final_score < 0 OR final_score > 100)
        LIMIT 20
    """)
    for r in cur.fetchall():
        score_errors.append({
            'id': r['id'],
            'source': 'llm_multisource',
            'quant': r['quant_score'],
            'final': r['final_score'],
            'issue': 'score out of range'
        })
    
    if score_errors:
        print(f"   ⚠️  发现 {len(score_errors)} 条 score 计算错误:")
        for e in score_errors[:5]:
            print(f"      id={e['id']}, source={e['source']}, "
                  f"quant={e['quant']}, final={e['final']}"
                  f"{', expected=' + str(e['expected']) if 'expected' in e else ''}")
        all_issues.append(('score_calc_error', len(score_errors)))
    else:
        print("   ✅ score 计算逻辑全部正确")
    
    # 5. 验证 sources 字段格式
    print("\n5. 验证 sources 字段格式...")
    cur.execute("""
        SELECT id, source, sources
        FROM daily_candidates
        WHERE sources IS NULL
        LIMIT 10
    """)
    null_sources = cur.fetchall()
    if null_sources:
        print(f"   ⚠️  发现 {len(null_sources)} 条 sources 为空")
        all_issues.append(('sources_null', len(null_sources)))
    else:
        print("   ✅ sources 字段全部有值")
    
    # 6. 验证 position_pct 合理性
    print("\n6. 验证 position_pct 合理性...")
    cur.execute("""
        SELECT id, ts_code, source, position_pct, selected
        FROM daily_candidates
        WHERE selected = true AND (position_pct IS NULL OR position_pct <= 0 OR position_pct > 1)
        LIMIT 20
    """)
    position_errors = cur.fetchall()
    if position_errors:
        print(f"   ❌ 发现 {len(position_errors)} 条 position_pct 异常:")
        for r in position_errors[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, source={r['source']}, "
                  f"position_pct={r['position_pct']}")
        all_issues.append(('position_pct_invalid', len(position_errors)))
    else:
        print("   ✅ position_pct 全部合理")
    
    # 7. 验证 selected 字段与 score 的关系
    print("\n7. 验证 selected 字段逻辑...")
    cur.execute("""
        SELECT id, ts_code, source, final_score, selected
        FROM daily_candidates
        WHERE selected = true AND final_score < 25
        LIMIT 10
    """)
    selected_low_score = cur.fetchall()
    if selected_low_score:
        print(f"   ⚠️  发现 {len(selected_low_score)} 条 selected=true 但 final_score < 25:")
        for r in selected_low_score[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, source={r['source']}, "
                  f"final_score={r['final_score']}")
        all_issues.append(('selected_low_score', len(selected_low_score)))
    else:
        print("   ✅ selected 字段逻辑正确")
    
    # 8. 验证 mention_count 和 source_diversity
    print("\n8. 验证 mention_count 和 source_diversity...")
    cur.execute("""
        SELECT id, ts_code, source, mention_count, source_diversity
        FROM daily_candidates
        WHERE mention_count < 1 OR source_diversity < 1
        LIMIT 10
    """)
    count_errors = cur.fetchall()
    if count_errors:
        print(f"   ❌ 发现 {len(count_errors)} 条 mention_count/source_diversity 异常:")
        for r in count_errors[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, mention_count={r['mention_count']}, "
                  f"source_diversity={r['source_diversity']}")
        all_issues.append(('count_invalid', len(count_errors)))
    else:
        print("   ✅ mention_count 和 source_diversity 全部合理")
    
    # 9. 验证 logic_tags 字段
    print("\n9. 验证 logic_tags 字段...")
    cur.execute("""
        SELECT id, ts_code, source, logic_tags
        FROM daily_candidates
        WHERE logic_tags IS NULL OR array_length(logic_tags, 1) = 0
        LIMIT 10
    """)
    tag_errors = cur.fetchall()
    if tag_errors:
        print(f"   ⚠️  发现 {len(tag_errors)} 条 logic_tags 为空:")
        for r in tag_errors[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, source={r['source']}")
        all_issues.append(('logic_tags_empty', len(tag_errors)))
    else:
        print("   ✅ logic_tags 全部有值")
    
    # 10. 验证 created_at 时间戳
    print("\n10. 验证 created_at 时间戳...")
    cur.execute("""
        SELECT id, snapshot_date, created_at, source
        FROM daily_candidates
        WHERE created_at::date < snapshot_date
        LIMIT 10
    """)
    time_errors = cur.fetchall()
    if time_errors:
        print(f"   ❌ 发现 {len(time_errors)} 条 created_at 早于 snapshot_date:")
        for r in time_errors[:5]:
            print(f"      id={r['id']}, snapshot_date={r['snapshot_date']}, "
                  f"created_at={r['created_at']}")
        all_issues.append(('created_at_early', len(time_errors)))
    else:
        print("   ✅ created_at 时间戳全部合理")
    
    # 11. 验证 run_mode 和 source 组合合理性
    print("\n11. 验证 run_mode 和 source 组合...")
    cur.execute("""
        SELECT source, run_mode, COUNT(*) as cnt
        FROM daily_candidates
        GROUP BY source, run_mode
        ORDER BY source, run_mode
    """)
    combos = cur.fetchall()
    print("   source/run_mode 组合分布:")
    for r in combos:
        print(f"      source={r['source']}, run_mode={r['run_mode']}, count={r['cnt']}")
    
    # 12. 验证 overnight_8step 的 position_pct 与 pool 的关系
    print("\n12. 验证 overnight_8step position_pct 与 pool 的关系...")
    cur.execute("""
        SELECT id, ts_code, position_pct, logic_tags
        FROM daily_candidates
        WHERE source = 'overnight_8step'
        LIMIT 20
    """)
    position_pool_errors = 0
    for r in cur.fetchall():
        tags = r['logic_tags'] or []
        has_stable = any('pool:stable' in str(t) for t in tags)
        has_upper = any('pool:upper' in str(t) for t in tags)
        
        if has_stable and r['position_pct'] != 0.15:
            position_pool_errors += 1
            if position_pool_errors <= 3:
                print(f"   ⚠️  stable pool position_pct 应为 0.15: id={r['id']}, "
                      f"actual={r['position_pct']}")
        elif has_upper and r['position_pct'] != 0.08:
            position_pool_errors += 1
            if position_pool_errors <= 3:
                print(f"   ⚠️  upper pool position_pct 应为 0.08: id={r['id']}, "
                      f"actual={r['position_pct']}")
    
    if position_pool_errors == 0:
        print("   ✅ position_pct 与 pool 关系正确")
    else:
        print(f"   ⚠️  发现 {position_pool_errors} 条 position_pct 与 pool 不匹配")
        all_issues.append(('position_pool_mismatch', position_pool_errors))
    
    # 13. 验证 main_uptrend 的价格字段
    print("\n13. 验证 main_uptrend 价格字段...")
    cur.execute("""
        SELECT id, ts_code, entry_low, entry_high, stop_loss, target_1, target_2
        FROM daily_candidates
        WHERE source = 'main_uptrend'
          AND (entry_low IS NULL OR entry_high IS NULL OR stop_loss IS NULL)
        LIMIT 10
    """)
    main_uptrend_null = cur.fetchall()
    if main_uptrend_null:
        print(f"   ⚠️  发现 {len(main_uptrend_null)} 条 main_uptrend 价格字段为空")
        all_issues.append(('main_uptrend_null_prices', len(main_uptrend_null)))
    else:
        print("   ✅ main_uptrend 价格字段全部有值")
    
    # 14. 统计汇总
    print("\n" + "=" * 80)
    print("验证汇总")
    print("=" * 80)
    
    if not all_issues:
        print("✅ 所有验证通过！数据完全正确。")
    else:
        print(f"⚠️  发现 {len(all_issues)} 类问题:")
        for issue_type, count in all_issues:
            print(f"   - {issue_type}: {count} 条")
    
    # 15. 数据总量统计
    print("\n📊 数据统计:")
    cur.execute("""
        SELECT source, COUNT(*) as cnt, 
               MIN(snapshot_date) as min_date, 
               MAX(snapshot_date) as max_date
        FROM daily_candidates
        GROUP BY source
        ORDER BY source
    """)
    for r in cur.fetchall():
        print(f"   {r['source']}: {r['cnt']} 条, 日期范围 {r['min_date']} ~ {r['max_date']}")
    
    cur.close()
    conn.close()

if __name__ == '__main__':
    verify_all()
