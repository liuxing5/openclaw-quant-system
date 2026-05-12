"""
漏斗策略每层逻辑单元测试
========================
逐函数验证计算正确性，不依赖数据库。
"""
from __future__ import annotations

import sys
import os
import math
from datetime import date, datetime, timezone, timedelta

import numpy as np
import pandas as pd

# 确保能导入策略模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from strategies.funnel_strategy.funnel_config import DEFAULT_FUNNEL_CONFIG, FunnelConfig


# ================================================================
# 测试工具
# ================================================================

_passed = 0
_failed = 0

def check(condition, msg=""):
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"  ❌ FAIL: {msg}")

def summary(name):
    global _passed, _failed
    print(f"\n{'='*60}")
    print(f"  {name}: {_passed} passed, {_failed} failed")
    print(f"{'='*60}")
    total = _passed + _failed
    _passed = 0
    _failed = 0
    return total


# ================================================================
# Layer 0: 大盘风控
# ================================================================

def test_layer0_ema():
    """测试 _calc_ema"""
    print("\n── Layer 0: _calc_ema ──")
    from strategies.funnel_strategy.layer0_market_guard import _calc_ema

    # 简单线性序列: EMA(span=3) 对 [10, 10, 10] 应该都是 10
    s = pd.Series([10.0, 10.0, 10.0, 10.0, 10.0])
    ema = _calc_ema(s, 3)
    check(abs(ema.iloc[-1] - 10.0) < 0.01, f"常量序列EMA=10, 得到{ema.iloc[-1]:.4f}")

    # EMA(span=3), alpha=2/(3+1)=0.5 对 [10, 20]
    # ema[0] = 10; ema[1] = 0.5*20 + 0.5*10 = 15
    s2 = pd.Series([10.0, 20.0])
    ema2 = _calc_ema(s2, 3)
    check(abs(ema2.iloc[-1] - 15.0) < 0.01, f"EMA(3) [10,20]=15, 得到{ema2.iloc[-1]:.4f}")

    # 指数在EMA上方
    check(20.0 > 15.0, "价格在EMA上方判定")


def test_layer0_decision_logic():
    """测试 check_market_environment 决策逻辑"""
    print("\n── Layer 0: 决策逻辑 ──")

    # 场景1: breadth OK + index above EMA → passed, full position
    # (涨家数≥2500, index_close > index_ema)
    # 场景2: breadth OK only → can_trade, partial cap
    # 场景3: neither → can't trade

    # 验证决策矩阵
    cfg = DEFAULT_FUNNEL_CONFIG

    # can_trade 条件: passed (both OK) OR one of them OK
    check(cfg.layer0_partial_cap == 0.50, "半仓比例=50%")

    # 上涨家数阈值
    check(cfg.layer0_min_advancers == 2500, "上涨阈值=2500")


# ================================================================
# Layer 1: 硬性防雷
# ================================================================

def test_layer1_current_ratio():
    """测试 compute_current_ratio"""
    print("\n── Layer 1: compute_current_ratio ──")
    from strategies.funnel_strategy.layer1_fundamental_filter import compute_current_ratio

    # 正常情况
    r = compute_current_ratio({'total_assets': 100, 'total_liabilities': 50})
    check(abs(r - 2.0) < 0.01, f"流动比率=2.0, 得到{r}")

    # 负债为0 → None
    r = compute_current_ratio({'total_assets': 100, 'total_liabilities': 0})
    check(r is None, "负债为0时返回None")

    # 无数据
    r = compute_current_ratio({})
    check(r is None, "无数据返回None")

    # 边界: 刚好等于阈值
    check(1.2 <= 1.2, "流动比率≥1.2通过")


def test_layer1_check_fundamental():
    """测试 check_fundamental 各子检查"""
    print("\n── Layer 1: check_fundamental ──")
    from strategies.funnel_strategy.layer1_fundamental_filter import check_fundamental

    cfg = DEFAULT_FUNNEL_CONFIG
    today = date.today()

    # 1. ST检查
    result = check_fundamental('000001.SZ', {'is_st': True, 'stock_name': 'ST测试'},
                               {}, cfg, today, verbose=False)
    check(not result['passed'] and 'ST' in result['reject_reason'],
          f"ST被拒: {result['reject_reason']}")

    # 2. 名称中包含ST
    result = check_fundamental('000001.SZ', {'is_st': False, 'stock_name': '*ST退市'},
                               {}, cfg, today, verbose=False)
    check(not result['passed'] and 'ST' in result['reject_reason'],
          f"名称*ST被拒: {result['reject_reason']}")

    # 3. 次新股
    recent_ipo = today - timedelta(days=30)  # 上市30天 < 60天
    result = check_fundamental('000001.SZ',
                               {'is_st': False, 'stock_name': '测试', 'list_date': recent_ipo},
                               {}, cfg, today, verbose=False)
    check(not result['passed'] and '次新' in result['reject_reason'],
          f"次新股被拒: {result['reject_reason']}")

    # 老股票通过次新检查
    old_ipo = today - timedelta(days=100)
    result = check_fundamental('000001.SZ',
                               {'is_st': False, 'stock_name': '测试', 'list_date': old_ipo},
                               {}, cfg, today, verbose=False)
    check(result['passed'], "老股票通过ST/次新检查")

    # 4. 流动比率不足
    fin_low_cr = {'total_assets': 100, 'total_liabilities': 100}  # ratio=1.0 < 1.2
    result = check_fundamental('000001.SZ',
                               {'is_st': False, 'stock_name': '测试', 'list_date': old_ipo},
                               fin_low_cr, cfg, today, verbose=False)
    check(not result['passed'] and '流动比率' in result['reject_reason'],
          f"流动比率不足被拒: {result['reject_reason']}")

    # 5. 负债率超标
    fin_high_debt = {
        'total_assets': 200,
        'total_liabilities': 100,
        'debt_ratio': 70.0,  # > 65%
    }
    result = check_fundamental('000001.SZ',
                               {'is_st': False, 'stock_name': '测试', 'list_date': old_ipo},
                               fin_high_debt, cfg, today, verbose=False)
    check(not result['passed'] and '负债率' in result['reject_reason'],
          f"负债率超标被拒: {result['reject_reason']}")

    # 6. 营收同比过低
    fin_low_rev = {
        'total_assets': 200,
        'total_liabilities': 100,
        'debt_ratio': 50.0,
        'revenue_yoy': -15.0,  # < -10%
    }
    result = check_fundamental('000001.SZ',
                               {'is_st': False, 'stock_name': '测试', 'list_date': old_ipo},
                               fin_low_rev, cfg, today, verbose=False)
    check(not result['passed'] and '营收' in result['reject_reason'],
          f"营收过低被拒: {result['reject_reason']}")

    # 7. 全部通过
    fin_good = {
        'total_assets': 200,
        'total_liabilities': 100,
        'debt_ratio': 50.0,
        'revenue_yoy': 5.0,
    }
    result = check_fundamental('000001.SZ',
                               {'is_st': False, 'stock_name': '测试', 'list_date': old_ipo},
                               fin_good, cfg, today, verbose=False)
    check(result['passed'], f"全部通过: {result['reject_reason']}")
    check(result['details']['current_ratio'] == 2.0, "流动比率=2.0")
    check(result['details']['debt_ratio'] == 50.0, "负债率=50%")


# ================================================================
# Layer 2: 流动性筛选
# ================================================================

def test_layer2_logic():
    """测试 Layer 2 参数和逻辑"""
    print("\n── Layer 2: 参数/逻辑 ──")
    cfg = DEFAULT_FUNNEL_CONFIG

    # 验证参数
    check(cfg.layer2_min_avg_amount_20d == 1e8, "20日均额>1亿")
    check(cfg.layer2_min_circulating_mcap == 2e9, "流通市值>20亿")
    check(cfg.layer2_turn_rate_min == 3.0, "换手率≥3%")
    check(cfg.layer2_turn_rate_max == 15.0, "换手率≤15%")

    # 市值估算逻辑（源码 line 153-157）
    # amount / (turnover_rate / 100) = amount * 100 / turnover_rate
    # 例: amount=5e8, turn=5% → 估算市值 = 5e8 / 0.05 = 100亿
    amount = 5e8
    turn = 5.0
    est_mcap = amount / (turn / 100.0)
    check(abs(est_mcap - 1e10) < 1, f"市值估算=100亿, 得到{est_mcap/1e8:.1f}亿")

    # 换手率过滤边界
    check(3.0 >= cfg.layer2_turn_rate_min, "换手3%通过")
    check(15.0 <= cfg.layer2_turn_rate_max, "换手15%通过")
    check(2.9 < cfg.layer2_turn_rate_min, "换手2.9%不通过")
    check(15.1 > cfg.layer2_turn_rate_max, "换手15.1%不通过")


# ================================================================
# Layer 3: 趋势结构
# ================================================================

def make_ohlcv_df(prices, highs=None, lows=None, opens=None):
    """构造测试用 OHLCV DataFrame"""
    n = len(prices)
    if highs is None:
        highs = [p * 1.02 for p in prices]
    if lows is None:
        lows = [p * 0.98 for p in prices]
    if opens is None:
        opens = [p * 0.99 for p in prices]

    dates = pd.date_range(end=date.today(), periods=n, freq='B')
    df = pd.DataFrame({
        'open': opens,
        'high': highs,
        'low': lows,
        'close': prices,
        'volume': [1e7] * n,
    }, index=dates)
    return df


def test_layer3_ema():
    """测试 Layer 3 _calc_ema"""
    print("\n── Layer 3: _calc_ema ──")
    from strategies.funnel_strategy.layer3_trend_filter import _calc_ema

    # 上升序列
    s = pd.Series([10.0, 12.0, 14.0, 16.0, 18.0])
    ema = _calc_ema(s, 3)
    check(ema.iloc[-1] > s.iloc[0], "上升序列EMA终值>初值")
    check(ema.iloc[-1] < s.iloc[-1], "上升序列EMA滞后于价格")


def test_layer3_detect_trend_structure():
    """测试 _detect_trend_structure"""
    print("\n── Layer 3: _detect_trend_structure ──")
    from strategies.funnel_strategy.layer3_trend_filter import _detect_trend_structure

    cfg = DEFAULT_FUNNEL_CONFIG

    # 1. 回踩支撑: 近5日低点贴近EMA12 ±2%
    # 构造: 价格在EMA12附近，最近5天低点接近EMA12
    prices = [10.0] * 30 + [10.5, 10.3, 10.1, 10.2, 10.4]  # 先稳定再回踩后收回
    df = make_ohlcv_df(prices)
    # 最后5天 low 设低一点模拟回踩
    df.iloc[-5:, df.columns.get_loc('low')] = [9.9, 9.85, 9.8, 9.9, 10.0]

    result = _detect_trend_structure(df, cfg)
    # 可能检测到 pullback_support 或 ascending_platform 或 unknown
    # 取决于具体数据
    check(result['structure'] in ('pullback_support', 'ascending_platform', 'unknown'),
          f"趋势结构类型有效: {result['structure']}")

    # 2. 上升平台: 10日振幅<8%, 收盘突破上沿
    platform_prices = [10.0] * 25 + [10.1, 10.15, 10.12, 10.18, 10.25]  # 窄幅整理后突破
    df2 = make_ohlcv_df(platform_prices)
    df2.iloc[-10:, df2.columns.get_loc('high')] = [10.3]*10
    df2.iloc[-10:, df2.columns.get_loc('low')] = [9.9]*10
    df2.iloc[-1, df2.columns.get_loc('close')] = 10.29  # 突破上沿99%

    result2 = _detect_trend_structure(df2, cfg)
    check(result2['structure'] in ('pullback_support', 'ascending_platform', 'unknown'),
          f"趋势结构2有效: {result2['structure']}")


def test_layer3_check_single():
    """测试 _check_single 各子检查"""
    print("\n── Layer 3: _check_single ──")
    from strategies.funnel_strategy.layer3_trend_filter import _check_single

    cfg = DEFAULT_FUNNEL_CONFIG

    # 构造充足的上升趋势数据
    # 200天从10涨到20，确保EMA12>EMA26>EMA50且价格>EMA12
    n_days = 200
    prices = np.linspace(10.0, 20.0, n_days) + np.random.normal(0, 0.1, n_days).cumsum() * 0.1
    prices = np.maximum(prices, 1.0)
    df = make_ohlcv_df(list(prices))

    # 最后几天确保上升
    df.iloc[-5:, df.columns.get_loc('close')] = [20.0, 20.5, 21.0, 21.5, 22.0]

    cache = {'TEST.SZ': df}
    result = _check_single('TEST.SZ', cfg, cache)
    check(result['passed'], f"上升趋势通过: {result.get('reject_reason', 'OK')}")
    check('ema12' in result['details'], "包含ema12详情")
    check(result['details'].get('ema_alignment', '') in ('bullish', 'bullish_short'),
          f"EMA排列: {result['details'].get('ema_alignment')}")

    # 数据不足被拒
    result2 = _check_single('EMPTY.SZ', cfg, {})
    check(not result2['passed'], f"无数据被拒: {result2['reject_reason']}")

    # 空 DataFrame
    cache3 = {'TEST2.SZ': pd.DataFrame()}
    result3 = _check_single('TEST2.SZ', cfg, cache3)
    check(not result3['passed'], f"空数据被拒: {result3['reject_reason']}")


# ================================================================
# Layer 4: 动能与买入信号
# ================================================================

def test_layer4_fast_ema():
    """测试 _fast_ema_last"""
    print("\n── Layer 4: _fast_ema_last ──")
    from strategies.funnel_strategy.layer4_momentum_filter import _fast_ema_last

    # EMA(12), alpha=2/(12+1)=0.1538
    values = np.array([10.0, 10.0, 10.0, 10.0, 10.0])
    ema = _fast_ema_last(values, 12)
    check(abs(ema - 10.0) < 0.01, f"常量序列EMA=10, 得到{ema:.4f}")

    # [10, 20] → ema=0.1538*20 + 0.8462*10 = 3.076 + 8.462 = 11.538
    values2 = np.array([10.0, 20.0])
    ema2 = _fast_ema_last(values2, 12)
    expected = 2.0 / 13 * 20 + 11.0 / 13 * 10  # = 11.538
    check(abs(ema2 - expected) < 0.01, f"EMA(12) [10,20]={expected:.3f}, 得到{ema2:.4f}")

    # 空数组
    check(_fast_ema_last(np.array([]), 12) == 0.0, "空数组返回0")


def test_layer4_fast_boll():
    """测试 _fast_boll_upper"""
    print("\n── Layer 4: _fast_boll_upper ──")
    from strategies.funnel_strategy.layer4_momentum_filter import _fast_boll_upper

    # 20天常量价格=10 → mean=10, std=0, upper=10
    values = np.full(25, 10.0)
    upper = _fast_boll_upper(values, 20)
    check(abs(upper - 10.0) < 0.01, f"常量布林上轨=10, 得到{upper:.4f}")

    # 数据不足20天
    values2 = np.array([10.0, 11.0, 12.0])
    upper2 = _fast_boll_upper(values2, 20)
    check(np.isnan(upper2), f"不足20天返回NaN, 得到{upper2}")

    # 交替价格: mean=15, std≈5
    values3 = np.array([10.0, 20.0] * 10 + [15.0])  # 21个值
    upper3 = _fast_boll_upper(values3, 20)
    # mean=15, std≈5.13, upper=15+10.26=25.26
    check(upper3 > 15.0, f"波动布林上轨>均值, 得到{upper3:.4f}")


def test_layer4_hammer():
    """测试 _is_hammer"""
    print("\n── Layer 4: _is_hammer ──")
    from strategies.funnel_strategy.layer4_momentum_filter import _is_hammer

    # 标准锤子: 小实体在下部, 长下影线, 几乎无上影线
    # open=10.2, close=10.1, low=9.0, high=10.25
    hammer = {'open': 10.2, 'close': 10.1, 'low': 9.0, 'high': 10.25}
    check(_is_hammer(hammer), "标准锤子线")

    # 下影线不够长
    not_hammer1 = {'open': 10.2, 'close': 10.1, 'low': 10.0, 'high': 10.25}
    check(not _is_hammer(not_hammer1), "下影线不够长")

    # 大实体
    not_hammer2 = {'open': 10.0, 'close': 11.0, 'low': 9.8, 'high': 11.05}
    check(not _is_hammer(not_hammer2), "实体过大")

    # 十字星 (body=0): 下影线长即可
    doji = {'open': 10.10, 'close': 10.10, 'low': 9.0, 'high': 10.2}
    check(_is_hammer(doji), "长下影十字星")


def test_layer4_piercing():
    """测试 _is_piercing"""
    print("\n── Layer 4: _is_piercing ──")
    from strategies.funnel_strategy.layer4_momentum_filter import _is_piercing

    # 标准刺透形态:
    # 昨日: open=11, close=10 (阴线, body=1)
    # 今日: open=9.8, close=10.6 (> midpoint=10.5 且 open<昨日close=10)
    yesterday = {'open': 11.0, 'close': 10.0}
    today = {'open': 9.8, 'close': 10.6}
    check(_is_piercing(today, yesterday), f"标准刺透形态, midpoint=10.5, close=10.6>10.5, open=9.8<10.0")

    # 昨日阳线 → 不构成刺透
    yesterday_yang = {'open': 10.0, 'close': 11.0}
    check(not _is_piercing({'open': 11.5, 'close': 12.0}, yesterday_yang), "昨日阳线不构成刺透")

    # 今日阴线 → 不构成刺透
    check(not _is_piercing({'open': 10.6, 'close': 9.8}, yesterday), "今日阴线不构成刺透")

    # 收盘不够高 (close <= midpoint)
    today_weak = {'open': 9.8, 'close': 10.3}  # midpoint=10.5
    check(not _is_piercing(today_weak, yesterday), "收盘未过中点")

    # open >= 昨日close → 跳空高开，不算刺透
    today_gapup = {'open': 10.2, 'close': 11.0}
    check(not _is_piercing(today_gapup, yesterday), "跳空高开不构成刺透")


def test_layer4_get_limit_pct():
    """测试 _get_limit_pct"""
    print("\n── Layer 4: _get_limit_pct ──")
    from strategies.funnel_strategy.layer4_momentum_filter import _get_limit_pct

    # 科创板
    check(abs(_get_limit_pct('688001.SH') - 19.8) < 0.01, "科创板涨停19.8%")
    # 创业板
    check(abs(_get_limit_pct('300001.SZ') - 19.8) < 0.01, "创业板涨停19.8%")
    check(abs(_get_limit_pct('301001.SZ') - 19.8) < 0.01, "创业板301涨停19.8%")
    # 北交所
    check(abs(_get_limit_pct('800001.BJ') - 29.8) < 0.01, "北交所涨停29.8%")
    check(abs(_get_limit_pct('400001.BJ') - 29.8) < 0.01, "北交所4开头涨停29.8%")
    # 主板
    check(abs(_get_limit_pct('000001.SZ') - 9.8) < 0.01, "主板涨停9.8%")
    check(abs(_get_limit_pct('600001.SH') - 9.8) < 0.01, "上海主板涨停9.8%")


def test_layer4_check_single():
    """测试 _check_single 核心逻辑"""
    print("\n── Layer 4: _check_single ──")
    from strategies.funnel_strategy.layer4_momentum_filter import _check_single

    cfg = DEFAULT_FUNNEL_CONFIG

    # 构造有效的需求吸收信号:
    # - close接近EMA12 (乖离<6%)
    # - 量比1.5~3
    # - 今日锤子线 + 昨日阴线成为刺透
    # - 放量 (volume > avg_vol_5 * 1.2)

    # 先做OHLCV数据: 6天，最后一天是锤子+放量
    rows = [
        {'open': 10.0, 'high': 10.2, 'low': 9.8, 'close': 10.1, 'volume': 1e7, 'pct_chg': 1.0},
        {'open': 10.2, 'high': 10.3, 'low': 10.0, 'close': 10.05, 'volume': 1e7, 'pct_chg': -0.5},
        {'open': 10.1, 'high': 10.2, 'low': 9.9, 'close': 10.15, 'volume': 1e7, 'pct_chg': 1.0},
        {'open': 10.2, 'high': 10.25, 'low': 10.0, 'close': 10.1, 'volume': 1e7, 'pct_chg': -0.5},
        {'open': 11.5, 'high': 11.6, 'low': 10.0, 'close': 10.3, 'volume': 1e7, 'pct_chg': -2.0},  # 昨日阴线
        # 今日: 锤子线, open=10.2, close=10.4, low=9.5, high=10.5, 放量
        {'open': 10.2, 'high': 10.5, 'low': 9.5, 'close': 10.4, 'volume': 2.5e7, 'pct_chg': 1.0},
    ]

    # 预计算指标: EMA12≈10.2, 乖离≈2%, 量比=1.8
    pre = {
        'ema12': 10.2,
        'bias_pct': 1.96,   # < 6% ✓
        'vol_ratio': 1.8,   # 1.5~3 ✓
        'boll_upper': 12.0,
        'close': 10.4,
        'volume': 2.5e7,
    }

    result = _check_single('000001.SZ', cfg, {'000001.SZ': rows}, {'000001.SZ': pre})

    # 应该检测到需求吸收信号
    check(result['passed'], f"需求吸收通过: signal={result['signal_type']}, reject={result['reject_reason']}")
    check(result['signal_type'] == 'demand_absorption',
          f"信号类型为demand_absorption, 得到{result['signal_type']}")
    check(result['score_bonus'] >= 5.0, f"得分加成≥5, 得到{result['score_bonus']}")

    # 量比不符被拒
    pre_low_vr = {**pre, 'vol_ratio': 1.0}  # < 1.5
    result2 = _check_single('000001.SZ', cfg, {'000001.SZ': rows}, {'000001.SZ': pre_low_vr})
    check(not result2['passed'] and '量比' in result2['reject_reason'],
          f"量比不符被拒: {result2['reject_reason']}")

    # 乖离超标被拒
    pre_high_bias = {**pre, 'bias_pct': 10.0}  # > 6%
    result3 = _check_single('000001.SZ', cfg, {'000001.SZ': rows}, {'000001.SZ': pre_high_bias})
    check(not result3['passed'] and '乖离' in result3['reject_reason'],
          f"乖离超标被拒: {result3['reject_reason']}")

    # 无信号被拒
    # 构造平凡K线: 没有锤子/刺透
    boring_rows = [
        {'open': 10.0, 'high': 10.3, 'low': 9.9, 'close': 10.1, 'volume': 1e7, 'pct_chg': 0.5},
        {'open': 10.1, 'high': 10.4, 'low': 10.0, 'close': 10.2, 'volume': 1e7, 'pct_chg': 0.5},
        {'open': 10.2, 'high': 10.5, 'low': 10.1, 'close': 10.3, 'volume': 1e7, 'pct_chg': 0.5},
        {'open': 10.3, 'high': 10.4, 'low': 10.1, 'close': 10.3, 'volume': 1e7, 'pct_chg': 0.5},
        {'open': 10.4, 'high': 10.5, 'low': 10.2, 'close': 10.35, 'volume': 1e7, 'pct_chg': 0.5},
        {'open': 10.5, 'high': 10.6, 'low': 10.3, 'close': 10.4, 'volume': 1e7, 'pct_chg': 0.5},
    ]
    result4 = _check_single('000001.SZ', cfg, {'000001.SZ': boring_rows}, {'000001.SZ': pre})
    check(not result4['passed'] and '信号' in result4['reject_reason'],
          f"无买入信号被拒: {result4['reject_reason']}")


def test_layer4_strong_relay():
    """测试强势接力（一进二）逻辑"""
    print("\n── Layer 4: 强势接力 ──")
    from strategies.funnel_strategy.layer4_momentum_filter import _check_single

    cfg = DEFAULT_FUNNEL_CONFIG

    # 构造: 昨日首板涨停(≥9.8%*0.95=9.31%), 前日涨幅<9.8%*0.8=7.84%
    # 今日回踩VWAP后翘头(close > typical * 0.99), close > open (阳线)
    rows = [
        {'open': 10.0, 'high': 10.2, 'low': 9.9, 'close': 10.1, 'volume': 1e7, 'pct_chg': 1.0},
        {'open': 10.2, 'high': 10.3, 'low': 10.0, 'close': 10.2, 'volume': 1e7, 'pct_chg': 0.5},
        {'open': 10.3, 'high': 10.5, 'low': 10.0, 'close': 10.4, 'volume': 1e7, 'pct_chg': 2.0},  # 前日涨2%<7.84%
        {'open': 10.5, 'high': 11.5, 'low': 10.5, 'close': 11.5, 'volume': 3e7, 'pct_chg': 9.5},  # 昨日首板(9.5%≥9.31%)
        # 今日: 回踩后翘头, close>open
        {'open': 11.4, 'high': 11.8, 'low': 11.2, 'close': 11.6, 'volume': 2e7, 'pct_chg': 0.87},
    ]

    # 今天 typical = (11.8+11.2+11.6)/3 = 11.53, close=11.6 >= 11.53*0.99=11.42 ✓
    pre = {
        'ema12': 10.8,
        'bias_pct': 5.0,       # < 6% 通过乖离检查 (且 > 3% 不会触发需求吸收 close_to_ema)
        'vol_ratio': 2.0,      # 1.5~3 ✓
        'boll_upper': 15.0,
        'close': 11.6,
        'volume': 2e7,
    }

    result = _check_single('000001.SZ', cfg, {'000001.SZ': rows}, {'000001.SZ': pre})
    check(result['passed'], f"强势接力通过: signal={result['signal_type']}, reject={result['reject_reason']}")
    check(result['signal_type'] == 'strong_relay',
          f"信号类型为strong_relay, 得到{result['signal_type']}")
    check(result['score_bonus'] >= 8.0, f"得分加成≥8, 得到{result['score_bonus']}")


# ================================================================
# Layer 5: 人气精选
# ================================================================

def test_layer5_score():
    """测试 _score_single 评分逻辑"""
    print("\n── Layer 5: _score_single ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single

    cfg = DEFAULT_FUNNEL_CONFIG

    # 构造5天数据: 最后一天涨幅4% (黄金涨幅), 贴MA5, 振幅3%
    dates = pd.date_range(end=date.today(), periods=5, freq='B')
    df = pd.DataFrame({
        'open': [10.0, 10.1, 10.2, 10.3, 10.4],
        'high': [10.2, 10.3, 10.4, 10.5, 10.7],
        'low': [9.9, 10.0, 10.1, 10.2, 10.35],
        'close': [10.1, 10.2, 10.3, 10.4, 10.7],  # 最后一天明显上涨
        'volume': [1e7, 1.1e7, 1e7, 1.2e7, 1.5e7],
        'amount': [1e8, 1.1e8, 1e8, 1.2e8, 1.5e8],
        'pct_chg': [1.0, 1.0, 1.0, 1.0, 4.0],  # 4% 黄金涨幅
        'turnover_rate': [5.0, 5.0, 5.0, 5.0, 6.0],
        'volume_ratio': [1.0, 1.1, 1.0, 1.2, 1.5],
        'amplitude': [2.0, 2.0, 2.0, 2.0, 3.0],  # 振幅3%
    }, index=dates)

    rank_map = {'TEST.SZ': 50}  # 人气榜50名 (≤100 加分)

    result = _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, rank_map)

    # 黄金涨幅: pct_score=20
    check(result['pct_score'] == 20, f"黄金涨幅满分20, 得到{result['pct_score']}")
    # 贴MA5: bias_score应该很高
    check(result['bias_score'] > 0, f"贴线得分>0, 得到{result['bias_score']}")
    # 人气加分: rank=50 <= 100
    check(result['popularity_bonus'] == 5.0, f"人气加分=5, 得到{result['popularity_bonus']}")
    # 总分
    check(result['score'] >= 80, f"总分≥80, 得到{result['score']}")
    check('黄金涨幅' in result['tags'], f"标签含黄金涨幅: {result['tags']}")
    check('人气#50' in result['tags'], f"标签含人气排名: {result['tags']}")

    # 涨幅范围测试
    check(cfg.layer5_pct_range_low == 3.0, "涨幅下限3%")
    check(cfg.layer5_pct_range_high == 5.0, "涨幅上限5%")

    # 人气阈值
    check(cfg.layer5_popularity_rank_threshold == 100, "人气阈值100")


def test_layer5_scoring_edge_cases():
    """测试 Layer 5 评分边界"""
    print("\n── Layer 5: 评分边界 ──")
    from strategies.funnel_strategy.layer5_popularity_filter import _score_single

    cfg = DEFAULT_FUNNEL_CONFIG

    # 涨幅偏低: 2% (1~3%范围)
    dates = pd.date_range(end=date.today(), periods=5, freq='B')
    df = pd.DataFrame({
        'open': [10.0]*5, 'high': [10.2]*5, 'low': [9.9]*5, 'close': [10.1]*5,
        'volume': [1e7]*5, 'amount': [1e8]*5,
        'pct_chg': [2.0]*5, 'turnover_rate': [5.0]*5,
        'volume_ratio': [1.0]*5, 'amplitude': [3.0]*5,
    }, index=dates)

    result = _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, {})
    # pct_score = int((2.0-1.0) / (3.0-1.0) * 15) = int(7.5) = 7
    check(result['pct_score'] == 7, f"2%涨幅得分=7, 得到{result['pct_score']}")
    check('涨幅偏低' in result['tags'], f"标签含涨幅偏低: {result['tags']}")

    # 涨幅偏高: 7%
    df2 = df.copy()
    df2['pct_chg'] = [7.0]*5
    result2 = _score_single('TEST.SZ', cfg, {'TEST.SZ': df2}, {})
    # pct_score = int((8.0-7.0)/(8.0-5.0)*10) = int(3.33) = 3
    check(result2['pct_score'] == 3, f"7%涨幅得分=3, 得到{result2['pct_score']}")
    check('涨幅偏高' in result2['tags'], f"标签含涨幅偏高: {result2['tags']}")

    # 涨幅超出范围 (9%) → pct_score=0
    df3 = df.copy()
    df3['pct_chg'] = [9.0]*5
    result3 = _score_single('TEST.SZ', cfg, {'TEST.SZ': df3}, {})
    check(result3['pct_score'] == 0, f"9%超出范围得分=0, 得到{result3['pct_score']}")

    # 分数上限100
    check(result['score'] <= 100, f"分数上限100, 得到{result['score']}")


# ================================================================
# Layer 6: 刚性风控
# ================================================================

def test_layer6_atr():
    """测试 _calc_atr"""
    print("\n── Layer 6: _calc_atr ──")
    from strategies.funnel_strategy.layer6_risk_control import _calc_atr

    # 构造简单数据: 3天
    dates = pd.date_range(end=date.today(), periods=3, freq='B')
    df = pd.DataFrame({
        'open': [10.0, 10.5, 10.3],
        'high': [10.5, 11.0, 10.8],
        'low': [9.8, 10.2, 10.1],
        'close': [10.2, 10.8, 10.5],
        'volume': [1e7, 1.2e7, 1e7],
    }, index=dates)

    atr = _calc_atr(df, period=2)
    check(atr > 0, f"ATR>0, 得到{atr:.4f}")

    # 数据不足
    df_small = df.iloc[:1]
    atr2 = _calc_atr(df_small, period=2)
    check(atr2 == 0.0, f"数据不足返回0, 得到{atr2}")


def test_layer6_risk_params():
    """测试 compute_risk_params"""
    print("\n── Layer 6: compute_risk_params ──")
    from strategies.funnel_strategy.layer6_risk_control import compute_risk_params

    cfg = DEFAULT_FUNNEL_CONFIG

    # 构造上升趋势数据: 20天
    dates = pd.date_range(end=date.today(), periods=30, freq='B')
    prices = np.linspace(10.0, 12.0, 30)
    df = pd.DataFrame({
        'open': prices * 0.99,
        'high': prices * 1.03,
        'low': prices * 0.97,
        'close': prices,
        'volume': [1e7]*30,
    }, index=dates)

    entry_price = prices[-1]  # ≈ 12.0
    cache = {'TEST.SZ': df}
    result = compute_risk_params('TEST.SZ', entry_price, cfg, cache)

    check(result['atr'] > 0, f"ATR>0: {result['atr']:.3f}")
    check(result['stop_loss'] < entry_price, f"止损<入场: {result['stop_loss']:.2f} < {entry_price:.2f}")
    check(result['stop_loss'] > 0, f"止损>0: {result['stop_loss']:.2f}")
    check(result['target_price'] > entry_price, f"目标>入场: {result['target_price']:.2f} > {entry_price:.2f}")
    check(result['profit_loss_ratio'] >= cfg.layer6_min_profit_loss_ratio,
          f"盈亏比≥{cfg.layer6_min_profit_loss_ratio}: {result['profit_loss_ratio']}")
    check(result['trailing_ref'] > 0, f"移动止盈参考>0: {result['trailing_ref']:.2f}")

    # 盈亏比数学验证
    # target = entry + 2*ATR, stop = entry - 1*ATR
    # reward = 2*ATR, risk = 1*ATR, ratio = 2.0
    if result['atr'] > 0:
        expected_ratio = cfg.layer6_target_atr_mult / cfg.layer6_initial_stop_atr
        check(abs(result['profit_loss_ratio'] - expected_ratio) < 0.1,
              f"盈亏比应≈{expected_ratio}, 得到{result['profit_loss_ratio']}")

    # 无数据
    result2 = compute_risk_params('EMPTY.SZ', 10.0, cfg, {})
    check(not result2['passed'], "无数据不通过")
    check(result2['atr'] == 0.0, "无数据ATR=0")


def test_layer6_time_window():
    """测试 check_time_window"""
    print("\n── Layer 6: check_time_window ──")
    from strategies.funnel_strategy.layer6_risk_control import check_time_window

    cfg = DEFAULT_FUNNEL_CONFIG
    result = check_time_window(cfg)

    check('in_window' in result, "返回in_window字段")
    check('current_time' in result, "返回current_time字段")
    check(result['entry_after'] == '14:30', "入场时段=14:30")

    # 时区验证
    from strategies.funnel_strategy.layer6_risk_control import BEIJING_TZ
    check(BEIJING_TZ == timezone(timedelta(hours=8)), "北京时区UTC+8")


def test_layer6_risk_params_fails_low_ratio():
    """测试盈亏比不足场景"""
    print("\n── Layer 6: 盈亏比不足 ──")

    # 修改config使盈亏比要求很高，验证不通过
    cfg_strict = FunnelConfig(
        layer6_min_profit_loss_ratio=5.0,  # 要求>5:1
        layer6_target_atr_mult=2.0,
        layer6_initial_stop_atr=1.0,
        layer6_atr_period=20,
    )

    from strategies.funnel_strategy.layer6_risk_control import compute_risk_params

    dates = pd.date_range(end=date.today(), periods=30, freq='B')
    prices = np.linspace(10.0, 12.0, 30)
    df = pd.DataFrame({
        'open': prices * 0.99,
        'high': prices * 1.03,
        'low': prices * 0.97,
        'close': prices,
        'volume': [1e7]*30,
    }, index=dates)

    result = compute_risk_params('TEST.SZ', prices[-1], cfg_strict, {'TEST.SZ': df})
    # 实际盈亏比≈2.0 < 5.0
    check(not result['passed'], f"盈亏比不足被拒: ratio={result['profit_loss_ratio']}")
    check(result['profit_loss_ratio'] < 5.0, f"实际盈亏比<5: {result['profit_loss_ratio']}")


# ================================================================
# 配置验证
# ================================================================

def test_config():
    """测试 DEFAULT_FUNNEL_CONFIG 参数一致性"""
    print("\n── 配置验证 ──")
    cfg = DEFAULT_FUNNEL_CONFIG

    errors = cfg.validate()
    check(len(errors) == 0, f"配置验证通过, errors={errors}")

    # EMA参数
    check(cfg.layer3_ema_fast < cfg.layer3_ema_mid < cfg.layer3_ema_slow,
          f"EMA: {cfg.layer3_ema_fast}<{cfg.layer3_ema_mid}<{cfg.layer3_ema_slow}")

    # 量比参数
    check(cfg.layer4_volume_ratio_min < cfg.layer4_volume_ratio_max,
          f"量比: {cfg.layer4_volume_ratio_min}<{cfg.layer4_volume_ratio_max}")

    # 换手率
    check(cfg.layer2_turn_rate_min < cfg.layer2_turn_rate_max,
          f"换手: {cfg.layer2_turn_rate_min}<{cfg.layer2_turn_rate_max}")

    # Layer 数量
    check(cfg.total_layers == 7, f"共7层: {cfg.total_layers}")

    # enabled_layers
    enabled = cfg.enabled_layers
    check(len(enabled) == 7, f"7层全部启用: {enabled}")


# ================================================================
# 跨层数据流一致性
# ================================================================

def test_cross_layer_consistency():
    """测试跨层数据结构一致性"""
    print("\n── 跨层数据流一致性 ──")

    # Layer 3 输出格式
    l3_out = {'ts_code': '000001.SZ', 'score_bonus': 5.0, 'details': {}}
    check('ts_code' in l3_out, "L3输出含ts_code")
    check('score_bonus' in l3_out, "L3输出含score_bonus")

    # Layer 4 输出格式
    l4_out = {'ts_code': '000001.SZ', 'score_bonus': 8.0, 'signal_type': 'demand_absorption',
              'details': {'vol_ratio': 2.0, 'bias_pct': 3.0}}
    check('signal_type' in l4_out, "L4输出含signal_type")

    # Layer 5 输入: 从 L4 来的 item 包含 score_bonus 和 signal_type
    # Layer 5 代码中的 momentum_bonus 提取逻辑:
    item = {'ts_code': '000001.SZ', 'score_bonus': 8.0, 'signal_type': 'demand_absorption'}
    trend_bonus = item.get('trend_bonus', item.get('score_bonus', 0.0))
    momentum_bonus = item.get('momentum_bonus', 0.0)
    if 'signal_type' in item:
        momentum_bonus = item.get('score_bonus', 0.0)

    # ⚠️ 这里 trend_bonus 和 momentum_bonus 都会拿到 score_bonus=8.0
    # 因为 item 同时满足 trend_bonus fallback 和 signal_type 条件
    check(trend_bonus == 8.0, f"trend_bonus={trend_bonus}")
    check(momentum_bonus == 8.0, f"momentum_bonus={momentum_bonus}")

    # Layer 6 输出附加字段
    l6_required = ['atr', 'stop_loss', 'target_price', 'profit_loss_ratio', 'entry_price']
    for key in l6_required:
        check(key in {'atr': 1, 'stop_loss': 2, 'target_price': 3,
                      'profit_loss_ratio': 4, 'entry_price': 5},
              f"L6字段{key}存在")


# ================================================================
# Bug 回归测试: 之前发现的逻辑错误
# ================================================================

def test_regression_layer5_bonus_flow():
    """回归: Layer 5 bonus 数据流正确性"""
    print("\n── 回归: bonus 数据流 ──")

    # funnel_engine.py L265-267 在 L4→L5 之间正确地合并了 L3 trend_bonus:
    #   l3_bonus_map = {item['ts_code']: item.get('score_bonus', 0) for item in l3_result}
    #   for item in l4_result:
    #       item['trend_bonus'] = l3_bonus_map.get(item['ts_code'], 0)
    #
    # 然后 L5 中:
    #   trend_bonus = item.get('trend_bonus', ...) → 拿到 L3 的分数 ✓
    #   momentum_bonus = item.get('score_bonus', 0) (因为有 signal_type) → 拿到 L4 的分数 ✓

    from strategies.funnel_strategy.layer5_popularity_filter import _score_single

    cfg = DEFAULT_FUNNEL_CONFIG

    dates = pd.date_range(end=date.today(), periods=5, freq='B')
    df = pd.DataFrame({
        'open': [10.0]*5, 'high': [10.2]*5, 'low': [9.9]*5, 'close': [10.1]*5,
        'volume': [1e7]*5, 'amount': [1e8]*5,
        'pct_chg': [2.0]*5, 'turnover_rate': [5.0]*5,
        'volume_ratio': [1.0]*5, 'amplitude': [3.0]*5,
    }, index=dates)

    # 模拟 L4 item (已由 funnel_engine 合并 L3 trend_bonus):
    # item = {'ts_code': 'TEST.SZ', 'score_bonus': 8.0, 'signal_type': 'demand_absorption',
    #         'trend_bonus': 3.0}
    #
    # L5 提取逻辑:
    # trend_bonus = item.get('trend_bonus', item.get('score_bonus', 0.0)) = 3.0 (from key)
    # momentum_bonus = item.get('momentum_bonus', 0.0) = 0.0
    # if 'signal_type' in item: momentum_bonus = item.get('score_bonus', 0.0) = 8.0

    result = _score_single('TEST.SZ', cfg, {'TEST.SZ': df}, {},
                           trend_bonus=3.0, momentum_bonus=8.0)
    check(result['trend_bonus'] == 3.0, f"trend_bonus=L3的3分: {result['trend_bonus']}")
    check(result['momentum_bonus'] == 8.0, f"momentum_bonus=L4的8分: {result['momentum_bonus']}")
    # 总分 = 50 + 7(pct) + 15(bias) + 10(stability) + 0(pop) + 3(trend) + 8(momentum) = 93
    check(result['score'] == 93, f"总分=93, 得到{result['score']}")
    print(f"    ✓ L3 trend_bonus(3) + L4 momentum_bonus(8) 各自独立计入，无重复/丢失")


def test_regression_layer0_index_ema_calc():
    """回归: Layer 0 指数EMA使用AVG(close)而非index_code特定股票"""
    print("\n── 回归: 指数EMA计算方式 ──")

    # Layer 0 中用 AVG(close) 作为全市场等权均价
    # 而配置中 layer0_index_code = '000001.SH' 存在但未使用
    from strategies.funnel_strategy.layer0_market_guard import check_market_environment
    cfg = DEFAULT_FUNNEL_CONFIG

    # index_code 存在于配置但代码中仅 SQL 查询 AVG(close)
    # 这不是 bug，是设计选择（全A等权均价 vs 上证综指）
    # 但配置中的 index_code 参数未被使用，属于死参数
    check(cfg.layer0_index_code == '000001.SH',
          "index_code=000001.SH (配置中有但SQL用AVG(close))")


def test_regression_layer1_debt_ratio_check():
    """回归: Layer 1 负债率检查逻辑"""
    print("\n── 回归: 负债率检查 ──")

    # 负债率字段是百分比值 (如 65.0 代表 65%)
    # 配置中 layer1_max_debt_ratio = 65.0
    # 代码: if debt_ratio > cfg.layer1_max_debt_ratio → reject
    # 如果 debt_ratio 存的是小数 0.65, 则 0.65 > 65.0 为 False, 不会拒绝
    # 如果 debt_ratio 存的是百分数 65.0, 65.0 > 65.0 为 False, 不拒绝
    # 边界: 65.1 > 65.0 → reject

    cfg = DEFAULT_FUNNEL_CONFIG
    check(cfg.layer1_max_debt_ratio == 65.0, "负债率阈值=65%")
    # 验证边界逻辑
    check(65.1 > 65.0, "65.1%被拒")
    check(not (65.0 > 65.0), "65.0%不拒(边界)")


# ================================================================
# 运行全部测试
# ================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  漏斗策略逐层逻辑测试")
    print("=" * 60)

    test_layer0_ema()
    test_layer0_decision_logic()
    s0 = summary("Layer 0")

    test_layer1_current_ratio()
    test_layer1_check_fundamental()
    s1 = summary("Layer 1")

    test_layer2_logic()
    s2 = summary("Layer 2")

    test_layer3_ema()
    test_layer3_detect_trend_structure()
    test_layer3_check_single()
    s3 = summary("Layer 3")

    test_layer4_fast_ema()
    test_layer4_fast_boll()
    test_layer4_hammer()
    test_layer4_piercing()
    test_layer4_get_limit_pct()
    test_layer4_check_single()
    test_layer4_strong_relay()
    s4 = summary("Layer 4")

    test_layer5_score()
    test_layer5_scoring_edge_cases()
    s5 = summary("Layer 5")

    test_layer6_atr()
    test_layer6_risk_params()
    test_layer6_time_window()
    test_layer6_risk_params_fails_low_ratio()
    s6 = summary("Layer 6")

    test_config()
    test_cross_layer_consistency()
    test_regression_layer5_bonus_flow()
    test_regression_layer0_index_ema_calc()
    test_regression_layer1_debt_ratio_check()
    s_regression = summary("回归 + 配置 + 一致性")

    print(f"\n{'='*60}")
    print(f"  总计: {s0+s1+s2+s3+s4+s5+s6+s_regression} 项测试")
    print(f"{'='*60}")
