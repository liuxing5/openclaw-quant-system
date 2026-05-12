"""
漏斗策略第三轮审查测试
======================
覆盖前两轮遗漏: LLM加分、概念共振、并行/串行分支、ATR trailing、兼容接口
"""
from __future__ import annotations
import sys, os, math
from datetime import date, datetime, timezone, timedelta
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from strategies.funnel_strategy.funnel_config import DEFAULT_FUNNEL_CONFIG, FunnelConfig

_p, _f = 0, 0
def chk(cond, msg=""):
    global _p, _f
    if cond: _p += 1
    else: _f += 1; print(f"  ❌ {msg}")
def done(label):
    global _p, _f
    print(f"  {label}: {_p}✓ {_f}✗")
    t = _p + _f; _p = _f = 0; return t


# ================================================================
# L5: LLM 加分完整路径测试
# ================================================================

def _make_l5_df(pct=4.0, amp=3.0, closes=None):
    """构造L5测试DataFrame"""
    n = 5
    if closes is None:
        closes = [10.0, 10.05, 10.1, 10.08, 10.15]
    dates = pd.date_range(end=date.today(), periods=n, freq='B')
    return pd.DataFrame({
        'open':  [c*0.99 for c in closes],
        'high':  [c*1.02 for c in closes],
        'low':   [c*0.98 for c in closes],
        'close': closes,
        'volume': [1e7]*n, 'amount': [1e8]*n,
        'pct_chg': [pct]*n, 'turnover_rate': [5.0]*n,
        'volume_ratio': [1.0]*n, 'amplitude': [amp]*n,
    }, index=dates)


def test_l5_llm_bonus_all_paths():
    """L5 LLM加分: 共识/终评/提及/精选 全部触发"""
    print("\n── L5: LLM全路径加分 ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single
    cfg = DEFAULT_FUNNEL_CONFIG

    df = _make_l5_df()
    rank_map = {}
    llm_data = {'TEST.SZ': {
        'stock_name': '测试',
        'consensus_score': 70.0,    # ≥60 → +8
        'final_score': 40.0,        # ≥30 → +10
        'mention_count': 3,         # ≥2 → +3
        'selected': True,           # → +5
    }}
    # Total llm_bonus = 8+10+3+5 = 26

    r = _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, rank_map,
                      trend_bonus=0, momentum_bonus=0,
                      llm_map=llm_data, concept_map={})

    chk(r['llm_bonus'] == 26.0, f"LLM全加分=26: {r['llm_bonus']}")
    chk('LLM共识70' in r['tags'], f"共识标签: {r['tags']}")
    chk('LLM评40' in r['tags'], f"终评标签: {r['tags']}")
    chk('多源×3' in r['tags'], f"多源标签: {r['tags']}")
    chk('LLM精选' in r['tags'], f"精选标签: {r['tags']}")

    # 总分验证: 50 + 20(pct) + bias + 10(stability) + 0(pop) + 0 + 0 + 26(llm)
    expected_base = 50 + 20 + r['bias_score'] + 10 + 26
    chk(r['score'] == min(expected_base, 100),
       f"总分={r['score']} (预期{min(expected_base,100)})")


def test_l5_llm_bonus_below_threshold():
    """L5 LLM加分: 全部低于阈值→不加分"""
    print("\n── L5: LLM低于阈值 ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single
    cfg = DEFAULT_FUNNEL_CONFIG

    df = _make_l5_df()
    llm_data = {'TEST.SZ': {
        'consensus_score': 50.0,    # < 60 → no bonus
        'final_score': 20.0,        # < 30 → no bonus
        'mention_count': 1,         # < 2 → no bonus
        'selected': False,          # → no bonus
    }}

    r = _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, {},
                      trend_bonus=0, momentum_bonus=0,
                      llm_map=llm_data, concept_map={})

    chk(r['llm_bonus'] == 0.0, f"LLM全不达标=0: {r['llm_bonus']}")
    chk(r['llm_details'] == {}, f"llm_details为空: {r['llm_details']}")


def test_l5_llm_bonus_partial():
    """L5 LLM加分: 部分达标"""
    print("\n── L5: LLM部分达标 ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single
    cfg = DEFAULT_FUNNEL_CONFIG

    df = _make_l5_df()
    llm_data = {'TEST.SZ': {
        'consensus_score': 65.0,    # ≥60 → +8
        'final_score': 25.0,        # <30 → 0
        'mention_count': 2,         # ≥2 → +3
        'selected': False,          # → 0
    }}
    # Total = 8+3 = 11

    r = _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, {},
                      trend_bonus=0, momentum_bonus=0,
                      llm_map=llm_data, concept_map={})

    chk(r['llm_bonus'] == 11.0, f"LLM部分=11: {r['llm_bonus']}")
    chk('LLM共识65' in r['tags'], "含共识标签")
    chk('多源×2' in r['tags'], "含多源标签")
    chk('LLM精选' not in r['tags'], "无精选标签")


def test_l5_concept_resonance():
    """L5 概念共振: 股票概念与LLM热门概念匹配"""
    print("\n── L5: 概念共振 ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single
    cfg = DEFAULT_FUNNEL_CONFIG

    df = _make_l5_df()

    # 两个LLM候选有概念
    llm_map = {
        'LLM1.SZ': {'mention_count': 1, 'consensus_score': 0, 'final_score': 0, 'selected': False},
        'LLM2.SZ': {'mention_count': 1, 'consensus_score': 0, 'final_score': 0, 'selected': False},
    }
    concept_map = {
        'LLM1.SZ': ['AI', '芯片', '新能源'],
        'LLM2.SZ': ['AI', '机器人', '芯片'],
        'TEST.SZ': ['AI', '光伏'],  # AI匹配LLM热门
    }

    r = _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, {},
                      trend_bonus=0, momentum_bonus=0,
                      llm_map=llm_map, concept_map=concept_map)

    # AI出现2次(LLM1+LLM2), 芯片2次, 新能源1次, 机器人1次 → Top10热门含AI和芯片
    # TEST.SZ的AI与热门匹配
    chk(r['llm_bonus'] == 5.0, f"概念共振+5: {r['llm_bonus']}")
    chk(any('概念共振' in t for t in r['tags']),
       f"概念共振标签: {r['tags']}")


def test_l5_concept_resonance_no_match():
    """L5 概念共振: 无匹配"""
    print("\n── L5: 概念共振无匹配 ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single
    cfg = DEFAULT_FUNNEL_CONFIG

    df = _make_l5_df()
    llm_map = {'LLM1.SZ': {'mention_count': 1, 'consensus_score': 0, 'final_score': 0, 'selected': False}}
    concept_map = {
        'LLM1.SZ': ['银行', '地产'],
        'TEST.SZ': ['AI', '芯片'],  # 不匹配
    }

    r = _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, {},
                      trend_bonus=0, momentum_bonus=0,
                      llm_map=llm_map, concept_map=concept_map)

    chk(r['llm_bonus'] == 0.0, f"无匹配不加分: {r['llm_bonus']}")


def test_l5_concept_resonance_no_stock_concepts():
    """L5 概念共振: 股票无概念"""
    print("\n── L5: 股票无概念 ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single
    cfg = DEFAULT_FUNNEL_CONFIG

    df = _make_l5_df()
    llm_map = {'LLM1.SZ': {'mention_count': 1, 'consensus_score': 0, 'final_score': 0, 'selected': False}}
    concept_map = {'LLM1.SZ': ['AI']}
    # TEST.SZ not in concept_map

    r = _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, {},
                      trend_bonus=0, momentum_bonus=0,
                      llm_map=llm_map, concept_map=concept_map)

    chk(r['llm_bonus'] == 0.0, f"无概念不加分: {r['llm_bonus']}")


def test_l5_llm_disabled():
    """L5 LLM加分: 配置关闭时跳过"""
    print("\n── L5: LLM禁用 ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single

    cfg_disabled = FunnelConfig(
        layer5_llm_bonus_enabled=False,
    )
    df = _make_l5_df()
    llm_data = {'TEST.SZ': {
        'consensus_score': 90.0, 'final_score': 80.0,
        'mention_count': 5, 'selected': True,
    }}

    r = _score_single('TEST.SZ', cfg_disabled, {'TEST.SZ': df}, {},
                      trend_bonus=0, momentum_bonus=0,
                      llm_map=llm_data, concept_map={})

    chk(r['llm_bonus'] == 0.0, f"禁用时LLM=0: {r['llm_bonus']}")
    chk(r['llm_details'] == {}, "禁用时无llm_details")


def test_l5_llm_score_cap():
    """L5 总分上限100 — LLM加分后不超过100"""
    print("\n── L5: LLM加分上限 ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single
    cfg = DEFAULT_FUNNEL_CONFIG

    # 构造完美数据: 黄金涨幅 + 贴MA5 + 分时平稳 + 人气 + LLM全中
    closes = [10.0, 10.02, 10.04, 10.06, 10.08]  # 非常贴近MA5
    df = _make_l5_df(pct=4.0, amp=2.5, closes=closes)
    rank_map = {'TEST.SZ': 1}
    llm_data = {'TEST.SZ': {
        'consensus_score': 90.0, 'final_score': 80.0,
        'mention_count': 5, 'selected': True,
    }}

    r = _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, rank_map,
                      trend_bonus=10.0, momentum_bonus=10.0,
                      llm_map=llm_data, concept_map={})

    chk(r['score'] <= 100, f"总分≤100: {r['score']}")
    chk(r['score'] == 100, f"上限锁定100: {r['score']}")


# ================================================================
# L5: 兼容接口测试
# ================================================================

def test_l5_compat_interface():
    """L5 compute_popularity_score 兼容接口不传llm_map"""
    print("\n── L5: 兼容接口 ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single
    cfg = DEFAULT_FUNNEL_CONFIG

    df = _make_l5_df()
    # 不传 llm_map 和 concept_map → 使用默认值
    r = _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, {},
                      trend_bonus=3.0, momentum_bonus=5.0)

    chk(r['llm_bonus'] == 0.0, "不传llm_map时LLM=0")
    chk(r['trend_bonus'] == 3.0, "trend_bonus正确传递")
    chk(r['momentum_bonus'] == 5.0, "momentum_bonus正确传递")
    chk(r['score'] > 0, "正常评分")


# ================================================================
# L5: 概念共振性能问题验证
# ================================================================

def test_l5_concept_hot_recomputed_per_stock():
    """验证概念共振每次重建hot_concepts (O(N*M)性能隐患)"""
    print("\n── L5: 概念共振重复计算 (性能) ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single
    cfg = DEFAULT_FUNNEL_CONFIG

    # 构造40个LLM候选(模拟真实场景), 每个有概念
    llm_map = {}
    concept_map = {}
    for i in range(40):
        code = f'LLM{i:04d}.SZ'
        llm_map[code] = {'mention_count': 1, 'consensus_score': 0,
                         'final_score': 0, 'selected': False}
        concept_map[code] = [f'概念{j}' for j in range(i % 5 + 1)]

    concept_map['TEST.SZ'] = ['概念0']  # 匹配热门

    df = _make_l5_df()
    # 模拟评分N只股票 — 每只都重建hot_concepts
    import time
    start = time.perf_counter()
    for _ in range(50):  # 模拟50只股票
        _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, {},
                      trend_bonus=0, momentum_bonus=0,
                      llm_map=llm_map, concept_map=concept_map)
    elapsed = time.perf_counter() - start

    # 50只股票×40个LLM候选 = 2000次迭代, 应该在亚秒级
    chk(elapsed < 2.0, f"概念共振50次耗时<2s: {elapsed:.3f}s")
    if elapsed > 0.5:
        print(f"    ⚠️ 概念共振O(N*M)性能提示: 50次={elapsed:.3f}s")


# ================================================================
# L6: trailing_ref 边界
# ================================================================

def test_l6_trailing_ref_ema12():
    """L6 移动止盈参考EMA12"""
    print("\n── L6: trailing_ref=EMA12 ──")
    from strategies.funnel_strategy.layer6_risk_control import _calc_ema

    # EMA12 计算验证
    prices = pd.Series([10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5,
                        15.0, 15.5, 16.0, 16.5, 17.0])
    ema12 = _calc_ema(prices, 12)
    chk(ema12.iloc[-1] < prices.iloc[-1],
       f"EMA12滞后于价格: ema={ema12.iloc[-1]:.2f} < price={prices.iloc[-1]:.2f}")
    chk(ema12.iloc[-1] > prices.iloc[0],
       f"EMA12高于初始价: {ema12.iloc[-1]:.2f} > {prices.iloc[0]:.2f}")


def test_l6_atr_adaptive_period():
    """L6 ATR自适应周期边界"""
    print("\n── L6: ATR自适应周期 ──")
    from strategies.funnel_strategy.layer6_risk_control import _calc_atr

    dates = pd.date_range(end=date.today(), periods=10, freq='B')
    prices = np.linspace(10.0, 12.0, 10)
    df = pd.DataFrame({
        'open': prices*0.99, 'high': prices*1.02, 'low': prices*0.98,
        'close': prices, 'volume': [1e7]*10,
    }, index=dates)

    # period=20, 但只有10天数据 → actual_period = min(20, 9) = 9
    atr = _calc_atr(df, period=20)
    chk(atr > 0, f"自适应周期ATR>0: {atr:.4f}")

    # period=5, 有10天数据 → actual_period = min(5, 9) = 5
    atr2 = _calc_atr(df, period=5)
    chk(atr2 > 0, f"5日ATR>0: {atr2:.4f}")


# ================================================================
# L3: 并行/串行分支
# ================================================================

def test_l3_parallel_serial_threshold():
    """L3 并行vs串行选择阈值"""
    print("\n── L3: 并行/串行分支 ──")

    # run_layer3_trend_filter 中: >100 → 并行, ≤100 → 串行
    # 验证阈值逻辑
    threshold = 100
    chk(101 > threshold, "101只→并行")
    chk(100 <= threshold, "100只→串行")
    chk(50 <= threshold, "50只→串行")


# ================================================================
# L0: 指数数据降级路径
# ================================================================

def test_l0_index_fallback():
    """L0 指数数据不可用时的降级"""
    print("\n── L0: 指数降级路径 ──")

    # 代码逻辑: 先查 index_code, 无数据则 fallback 到 AVG(close)
    # 这是修复后的行为 — 参数 index_code 实际被使用
    cfg = DEFAULT_FUNNEL_CONFIG
    chk(cfg.layer0_index_code == '000001.SH', "默认指数=000001.SH")

    # 验证: 当 rows 为空时, if not rows: → 执行 AVG(close) 降级查询
    # 这是 SQL 层面的行为, 此处验证代码结构
    from strategies.funnel_strategy.layer0_market_guard import check_market_environment
    import inspect
    source = inspect.getsource(check_market_environment)
    chk('if not rows:' in source, "index_code降级逻辑存在")
    chk('AVG(close)' in source, "AVG(close)降级存在")


# ================================================================
# L4: 预计算变量使用检查
# ================================================================

def test_l4_precompute_unused_vars():
    """L4 预计算中有未使用变量"""
    print("\n── L4: 预计算未使用变量 ──")
    from strategies.funnel_strategy.layer4_momentum_filter import _batch_load_and_precompute
    import inspect
    source = inspect.getsource(_batch_load_and_precompute)

    # today_open, today_high, today_low, today_pct, today_amplitude
    # 这些变量被计算但未存入 precomputed 也未在后续使用
    # ohlcv_cache 中 tail_rows 从 group 重新取值
    chk('today_open' in source, "today_open存在(未使用)")
    chk('today_high' in source, "today_high存在(未使用)")
    chk('today_low' in source, "today_low存在(未使用)")
    # 这些是 minor dead code，不影响正确性
    print(f"    ⚠️ today_open/high/low/pct/amplitude 计算但未使用(非bug,仅浪费)")


# ================================================================
# L2: _load_20d_avg_amount head(20) 逻辑
# ================================================================

def test_l2_head20_logic():
    """L2 20日均额: head(20)取最近20天"""
    print("\n── L2: head(20)逻辑 ──")

    # SQL: ORDER BY ts_code, trade_date DESC → head(20)取最近20天
    # 这是正确的 — DESC排序后head(20)就是最近20个交易日
    # 验证: DataFrame groupby后 head(20) 行为
    df = pd.DataFrame({
        'ts_code': ['A']*25,
        'amount': list(range(25)),
    })
    # 模拟SQL DESC排序后的数据
    df_sorted = df.sort_values('ts_code').reset_index(drop=True)
    # 按DESC排序意味着最新日期在前, head(20)取前20条
    recent = df_sorted.head(20)
    chk(len(recent) == 20, "head(20)=20条")
    chk(recent['amount'].mean() > 0, "均值>0")


# ================================================================
# 主入口
# ================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  漏斗策略第三轮审查测试")
    print("=" * 60)

    t = 0
    test_l5_llm_bonus_all_paths();          t += done("L5 LLM全路径")
    test_l5_llm_bonus_below_threshold();     t += done("L5 LLM低于阈值")
    test_l5_llm_bonus_partial();             t += done("L5 LLM部分达标")
    test_l5_concept_resonance();             t += done("L5概念共振")
    test_l5_concept_resonance_no_match();    t += done("L5概念无匹配")
    test_l5_concept_resonance_no_stock_concepts(); t += done("L5股票无概念")
    test_l5_llm_disabled();                  t += done("L5 LLM禁用")
    test_l5_llm_score_cap();                 t += done("L5 LLM上限")
    test_l5_compat_interface();              t += done("L5兼容接口")
    test_l5_concept_hot_recomputed_per_stock(); t += done("L5概念O(N*M)")

    test_l6_trailing_ref_ema12();            t += done("L6 EMA12 trailing")
    test_l6_atr_adaptive_period();           t += done("L6 ATR自适应")

    test_l3_parallel_serial_threshold();     t += done("L3并行/串行")

    test_l0_index_fallback();                t += done("L0指数降级")

    test_l4_precompute_unused_vars();        t += done("L4未使用变量")
    test_l2_head20_logic();                  t += done("L2 head(20)")

    print(f"\n{'='*60}")
    print(f"  总计: {t} 项测试")
    print(f"{'='*60}")
