"""
全面验证 daily_candidates 表中每个字段的正确性
=========================================================
基于 BaoStock 数据源和 schema 定义，检查：
1. ts_code 格式
2. stock_name 与 stock_basic_info 对比
3. mention_count 和 source_diversity 合理性
4. consensus_score, llm_score, quant_score, final_score 范围和逻辑
5. logic_tags 格式
6. selected 与 score 的关系
7. position_pct 合理性
8. entry_low/entry_high/stop_loss/target_1/target_2 价格计算
9. sources JSONB 格式
10. run_mode 和 source 组合
11. created_at 时间戳
12. 各策略特定字段验证
"""
import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor

os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '6543'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from core.db.connection import get_db_fresh

def verify_all():
    conn = get_db_fresh()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    print("=" * 80)
    print("全面验证 daily_candidates 数据正确性")
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
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条无效 ts_code")
        for r in invalid[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}")
        all_issues.append(('invalid_ts_code', len(invalid)))
    else:
        print("   ✅ ts_code 格式全部正确")
    
    # 2. 验证 stock_name
    print("\n2. 验证 stock_name...")
    cur.execute("""
        SELECT dc.id, dc.ts_code, dc.stock_name as dc_name, sbi.stock_name as sbi_name
        FROM daily_candidates dc
        LEFT JOIN stock_basic_info sbi ON dc.ts_code = sbi.ts_code
        WHERE (dc.stock_name IS NOT NULL AND dc.stock_name != '' AND sbi.stock_name IS NOT NULL AND dc.stock_name != sbi.stock_name)
           OR (dc.stock_name IS NULL AND sbi.stock_name IS NOT NULL)
        LIMIT 20
    """)
    mismatches = cur.fetchall()
    if mismatches:
        print(f"   ❌ 发现 {len(mismatches)} 条 stock_name 问题")
        for r in mismatches[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, DB={r['dc_name']}, BasicInfo={r['sbi_name']}")
        all_issues.append(('stock_name_issue', len(mismatches)))
    else:
        print("   ✅ stock_name 全部正确")
    
    # 3. 验证 mention_count 和 source_diversity
    print("\n3. 验证 mention_count 和 source_diversity...")
    cur.execute("""
        SELECT id, ts_code, source, mention_count, source_diversity
        FROM daily_candidates
        WHERE mention_count < 1 OR source_diversity < 1
           OR mention_count IS NULL OR source_diversity IS NULL
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 mention_count/source_diversity 异常")
        for r in invalid[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, mention={r['mention_count']}, diversity={r['source_diversity']}")
        all_issues.append(('mention_diversity_invalid', len(invalid)))
    else:
        print("   ✅ mention_count 和 source_diversity 全部合理")
    
    # 4. 验证 consensus_score
    print("\n4. 验证 consensus_score...")
    cur.execute("""
        SELECT id, ts_code, source, consensus_score
        FROM daily_candidates
        WHERE consensus_score IS NULL OR consensus_score < 0 OR consensus_score > 100
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 consensus_score 异常")
        for r in invalid[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, consensus={r['consensus_score']}")
        all_issues.append(('consensus_score_invalid', len(invalid)))
    else:
        print("   ✅ consensus_score 全部合理")
    
    # 5. 验证 llm_score
    print("\n5. 验证 llm_score...")
    cur.execute("""
        SELECT id, ts_code, source, llm_score
        FROM daily_candidates
        WHERE llm_score IS NULL OR llm_score < 0
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 llm_score 异常")
        for r in invalid[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, llm={r['llm_score']}")
        all_issues.append(('llm_score_invalid', len(invalid)))
    else:
        print("   ✅ llm_score 全部合理")
    
    # 6. 验证 quant_score
    print("\n6. 验证 quant_score...")
    cur.execute("""
        SELECT id, ts_code, source, quant_score
        FROM daily_candidates
        WHERE quant_score IS NULL OR quant_score < 0
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 quant_score 异常")
        for r in invalid[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, quant={r['quant_score']}")
        all_issues.append(('quant_score_invalid', len(invalid)))
    else:
        print("   ✅ quant_score 全部合理")
    
    # 7. 验证 final_score
    print("\n7. 验证 final_score...")
    cur.execute("""
        SELECT id, ts_code, source, final_score
        FROM daily_candidates
        WHERE final_score IS NULL OR final_score < 0 OR final_score > 100
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 final_score 异常")
        for r in invalid[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, final={r['final_score']}")
        all_issues.append(('final_score_invalid', len(invalid)))
    else:
        print("   ✅ final_score 全部合理")
    
    # 8. 验证 overnight_8step: final_score 应等于 min(quant_score, 100)
    print("\n8. 验证 overnight_8step final_score = min(quant_score, 100)...")
    cur.execute("""
        SELECT id, ts_code, quant_score, final_score
        FROM daily_candidates
        WHERE source = 'overnight_8step'
          AND quant_score IS NOT NULL
          AND final_score IS NOT NULL
          AND final_score != LEAST(quant_score, 100)
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 overnight_8step final_score != min(quant_score, 100)")
        for r in invalid[:5]:
            print(f"      id={r['id']}, quant={r['quant_score']}, final={r['final_score']}")
        all_issues.append(('overnight_score_mismatch', len(invalid)))
    else:
        print("   ✅ overnight_8step final_score = min(quant_score, 100)")
    
    # 9. 验证 logic_tags
    print("\n9. 验证 logic_tags...")
    cur.execute("""
        SELECT id, ts_code, source, logic_tags
        FROM daily_candidates
        WHERE logic_tags IS NULL OR array_length(logic_tags, 1) = 0
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 logic_tags 为空")
        for r in invalid[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}")
        all_issues.append(('logic_tags_empty', len(invalid)))
    else:
        print("   ✅ logic_tags 全部有值")
    
    # 10. 验证 selected 与 score 的关系
    print("\n10. 验证 selected 与 score 的关系...")
    cur.execute("""
        SELECT id, ts_code, source, final_score, selected
        FROM daily_candidates
        WHERE (selected = true AND final_score < 25)
           OR (selected = false AND final_score >= 70)
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ⚠️  发现 {len(invalid)} 条 selected 与 score 不一致")
        for r in invalid[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, selected={r['selected']}, score={r['final_score']}")
        all_issues.append(('selected_score_inconsistent', len(invalid)))
    else:
        print("   ✅ selected 与 score 关系合理")
    
    # 11. 验证 position_pct
    print("\n11. 验证 position_pct...")
    cur.execute("""
        SELECT id, ts_code, source, position_pct, selected
        FROM daily_candidates
        WHERE selected = true AND (position_pct IS NULL OR position_pct <= 0 OR position_pct > 1)
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 position_pct 异常")
        for r in invalid[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, position_pct={r['position_pct']}")
        all_issues.append(('position_pct_invalid', len(invalid)))
    else:
        print("   ✅ position_pct 全部合理")
    
    # 12. 验证价格字段计算
    print("\n12. 验证价格字段计算...")
    cur.execute("""
        SELECT dc.id, dc.ts_code, dc.snapshot_date, dc.source,
               dc.entry_low, dc.entry_high, dc.stop_loss, dc.target_1, dc.target_2,
               dq.close
        FROM daily_candidates dc
        JOIN daily_quotes dq ON dc.ts_code = dq.ts_code AND dc.snapshot_date = dq.trade_date
        WHERE dc.entry_low IS NOT NULL
        ORDER BY dc.id DESC
        LIMIT 200
    """)
    price_records = cur.fetchall()
    
    price_errors = []
    for r in price_records:
        close = float(r['close'])
        pure_code = r['ts_code'].split('.')[0]
        is_kc_cy = pure_code.startswith(('688', '300', '301'))
        
        if is_kc_cy:
            expected = {
                'entry_low': round(close * 0.985, 2),
                'entry_high': round(close * 1.015, 2),
                'stop_loss': round(close * 0.95, 2),
                'target_1': round(close * 1.08, 2),
                'target_2': round(close * 1.15, 2),
            }
        else:
            expected = {
                'entry_low': round(close * 0.99, 2),
                'entry_high': round(close * 1.01, 2),
                'stop_loss': round(close * 0.97, 2),
                'target_1': round(close * 1.05, 2),
                'target_2': round(close * 1.10, 2),
            }
        
        for field, expected_val in expected.items():
            actual = r[field]
            if actual:
                actual = float(actual)
                error_pct = abs(actual - expected_val) / expected_val if expected_val != 0 else 0
                if error_pct > 0.01:
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
        print(f"   ❌ 发现 {len(price_errors)} 条价格计算误差")
        for e in price_errors[:5]:
            print(f"      id={e['id']}, {e['ts_code']}, {e['field']} actual={e['actual']}, expected={e['expected']}, error={e['error_pct']:.2%}")
        all_issues.append(('price_calc_error', len(price_errors)))
    else:
        print("   ✅ 价格字段计算全部正确")
    
    # 13. 验证 sources JSONB 格式
    print("\n13. 验证 sources JSONB 格式...")
    # llm_multisource/overnight_8step/funnel_strategy 应为 array
    # main_uptrend 为 object (包含 b_factors, c_factors)
    cur.execute("""
        SELECT id, ts_code, source, sources
        FROM daily_candidates
        WHERE (source != 'main_uptrend' AND (sources IS NULL OR jsonb_typeof(sources) != 'array' OR jsonb_array_length(sources) = 0))
           OR (source = 'main_uptrend' AND (sources IS NULL OR jsonb_typeof(sources) != 'object'))
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 sources 格式异常")
        for r in invalid[:5]:
            print(f"      id={r['id']}, ts_code={r['ts_code']}, source={r['source']}")
        all_issues.append(('sources_invalid', len(invalid)))
    else:
        print("   ✅ sources JSONB 格式全部正确")
    
    # 14. 验证 run_mode 和 source 组合
    print("\n14. 验证 run_mode 和 source 组合...")
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
    
    # 15. 验证 overnight_8step position_pct 与 pool 的关系
    print("\n15. 验证 overnight_8step position_pct 与 pool 的关系...")
    cur.execute("""
        SELECT id, ts_code, position_pct, logic_tags
        FROM daily_candidates
        WHERE source = 'overnight_8step'
    """)
    pool_errors = 0
    for r in cur.fetchall():
        tags = r['logic_tags'] or []
        has_stable = any('pool:stable' in str(t) for t in tags)
        has_upper = any('pool:upper' in str(t) for t in tags)
        
        if has_stable and r['position_pct'] != 0.15:
            pool_errors += 1
            if pool_errors <= 3:
                print(f"   ⚠️  stable pool position_pct 应为 0.15: id={r['id']}, actual={r['position_pct']}")
        elif has_upper and r['position_pct'] != 0.08:
            pool_errors += 1
            if pool_errors <= 3:
                print(f"   ⚠️  upper pool position_pct 应为 0.08: id={r['id']}, actual={r['position_pct']}")
    
    if pool_errors == 0:
        print("   ✅ position_pct 与 pool 关系正确")
    else:
        print(f"   ⚠️  发现 {pool_errors} 条 position_pct 与 pool 不匹配")
        all_issues.append(('position_pool_mismatch', pool_errors))
    
    # 16. 验证 main_uptrend 价格字段
    print("\n16. 验证 main_uptrend 价格字段...")
    cur.execute("""
        SELECT id, ts_code, entry_low, entry_high, stop_loss, target_1, target_2
        FROM daily_candidates
        WHERE source = 'main_uptrend'
          AND (entry_low IS NULL OR entry_high IS NULL OR stop_loss IS NULL)
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 main_uptrend 价格字段为空")
        all_issues.append(('main_uptrend_null_prices', len(invalid)))
    else:
        print("   ✅ main_uptrend 价格字段全部有值")
    
    # 17. 验证 created_at 时间戳
    print("\n17. 验证 created_at 时间戳...")
    cur.execute("""
        SELECT id, snapshot_date, created_at, source
        FROM daily_candidates
        WHERE created_at::date < snapshot_date
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 created_at 早于 snapshot_date")
        for r in invalid[:5]:
            print(f"      id={r['id']}, snapshot_date={r['snapshot_date']}, created_at={r['created_at']}")
        all_issues.append(('created_at_early', len(invalid)))
    else:
        print("   ✅ created_at 时间戳全部合理")
    
    # 18. 验证 funnel_strategy 字段
    print("\n18. 验证 funnel_strategy 字段...")
    cur.execute("""
        SELECT id, ts_code, mention_count, source_diversity, consensus_score
        FROM daily_candidates
        WHERE source = 'funnel_strategy'
          AND (mention_count < 1 OR source_diversity < 1 OR consensus_score < 0)
        LIMIT 20
    """)
    invalid = cur.fetchall()
    if invalid:
        print(f"   ❌ 发现 {len(invalid)} 条 funnel_strategy 字段异常")
        all_issues.append(('funnel_strategy_invalid', len(invalid)))
    else:
        print("   ✅ funnel_strategy 字段全部合理")
    
    # 汇总
    print("\n" + "=" * 80)
    print("验证汇总")
    print("=" * 80)
    
    if not all_issues:
        print("✅ 所有验证通过！数据完全正确。")
    else:
        print(f"⚠️  发现 {len(all_issues)} 类问题:")
        for issue_type, count in all_issues:
            print(f"   - {issue_type}: {count} 条")
    
    # 数据统计
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
    
    return all_issues

if __name__ == '__main__':
    issues = verify_all()
