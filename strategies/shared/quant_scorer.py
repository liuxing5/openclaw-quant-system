"""
共享量化评分模块 — 统一 zuiyou1 / aggregate / funnel 的量化评分逻辑
=================================================================
v1.0: 从 zuiyou1 和 aggregate 中提取重复的量化评分代码，
      统一评分权重和阈值，避免维护不一致。

评分维度（总分100）:
  - 涨幅评分: 0-30
  - 换手率评分: 0-30
  - 成交额评分: 0-40
  - 振幅评分: 0-10
  - 量比评分: 0-15
  - 委比评分: -5~10
  - 大单净量: -10~15
  - 主力资金: -5~10
  - 强势股排名: 0-30
  - 机构预期: 0-25
  - 概念板块: 0-10
  - PE/PB估值: -30~25
  - 财务质量: -30~31
"""
import math
from typing import Dict, Optional, Tuple, List


def score_pct_chg(pct_chg: float, is_kc_cy: bool = False) -> Tuple[float, List[str]]:
    if -3 < pct_chg < 7:
        s = 30 * (1 - abs(pct_chg - 2) / 5)
    elif 7 <= pct_chg:
        limit = 19.5 if is_kc_cy else 9.5
        if pct_chg < limit:
            s = 10
        else:
            s = max(0, 10 - 30)
    else:
        s = 0
    tags = []
    if 1.5 <= pct_chg <= 4:
        tags.append("稳健蓄势")
    elif 4 < pct_chg <= 7:
        tags.append("强势突破")
    return max(0, s), tags


def score_turnover(turnover: float) -> Tuple[float, List[str]]:
    s = 0.0
    tags = []
    if turnover > 0:
        s = min(30, turnover * 2)
    if 5.0 <= turnover <= 8.0:
        tags.append("黄金换手")
    elif 8.0 < turnover <= 10.0:
        tags.append("换手偏高")
    elif turnover > 10:
        tags.append("换手过热")
    return s, tags


def score_amount(amount: float) -> Tuple[float, List[str]]:
    s = 0.0
    tags = []
    if amount > 0:
        s = min(40, 10 * math.log10(amount / 1e8 + 1))
    if amount > 5e8:
        tags.append("大成交额")
    return s, tags


def score_amplitude(amplitude: float) -> Tuple[float, List[str]]:
    s = 0.0
    tags = []
    if 3 <= amplitude <= 8:
        s = 10 * (1 - abs(amplitude - 5.5) / 2.5)
        tags.append("振幅活跃")
    elif amplitude > 8:
        s = 5
        tags.append("振幅偏大")
    return s, tags


def score_volume_ratio(volume_ratio: float) -> Tuple[float, List[str]]:
    s = 0.0
    tags = []
    if volume_ratio > 1.5:
        s = min(15, (volume_ratio - 1.5) * 10)
        tags.append("放量确认")
    elif volume_ratio < 0.8:
        s = -5
        tags.append("缩量")
    return s, tags


def score_commission_ratio(commission_ratio: float) -> Tuple[float, List[str]]:
    s = 0.0
    tags = []
    if commission_ratio > 0:
        s = min(10, commission_ratio / 10)
        tags.append("买盘强劲")
    elif commission_ratio < -20:
        s = -5
        tags.append("卖压大")
    return s, tags


def score_large_order_net(large_order_net: float) -> Tuple[float, List[str]]:
    s = 0.0
    tags = []
    if large_order_net > 0:
        s = min(15, large_order_net * 5)
        tags.append("大单净流入")
    elif large_order_net < -5:
        s = -10
        tags.append("大单净流出")
    return s, tags


def score_main_force_net(main_force_net: float) -> Tuple[float, List[str]]:
    s = 0.0
    tags = []
    if main_force_net > 0:
        s = min(10, main_force_net / 1e6 * 2)
        tags.append("主力流入")
    elif main_force_net < -1e7:
        s = -5
        tags.append("主力流出")
    return s, tags


def score_strong_rank(strong_ranks: Optional[list]) -> Tuple[float, List[str]]:
    if not strong_ranks:
        return 0.0, []
    best_rank_bonus = 0.0
    for sr in strong_ranks:
        pos = sr.get('rank_position') or 999
        if pos <= 10:
            bonus = 20
        elif pos <= 30:
            bonus = 10
        elif pos <= 50:
            bonus = 5
        else:
            bonus = 0
        best_rank_bonus = max(best_rank_bonus, bonus)
    consecutive_days = max((sr.get('consecutive_days') or 0) for sr in strong_ranks)
    if consecutive_days >= 5:
        best_rank_bonus += 10
    elif consecutive_days >= 3:
        best_rank_bonus += 5
    tags = [f"强势股+{best_rank_bonus:.0f}"] if best_rank_bonus > 0 else []
    return best_rank_bonus, tags


def score_earnings(fc: Optional[dict]) -> Tuple[float, List[str]]:
    if not fc:
        return 0.0, []
    bonus = 0.0
    inst_count = fc.get('institution_count') or 0
    if inst_count >= 10:
        bonus += 15
    elif inst_count >= 5:
        bonus += 8
    eps_mean = fc.get('eps_mean')
    industry_avg = fc.get('industry_avg')
    if eps_mean and industry_avg and industry_avg > 0:
        eps_premium = (eps_mean - industry_avg) / industry_avg
        if eps_premium > 0.1:
            bonus += 10
        elif eps_premium > 0.05:
            bonus += 5
    tags = [f"机构预期+{bonus:.0f}"] if bonus > 0 else []
    return bonus, tags


def score_concept(concepts: Optional[list], concept_cache: Optional[dict] = None) -> Tuple[float, List[str]]:
    if not concepts or not concept_cache:
        return 0.0, []
    best_bonus = 0.0
    for c in concepts:
        concept_data = concept_cache.get(c.get('concept_code'))
        if concept_data:
            concept_pct = concept_data.get('pct_chg') or 0
            if concept_pct > 3:
                bonus = 10
            elif concept_pct > 1:
                bonus = 5
            else:
                bonus = 0
            best_bonus = max(best_bonus, bonus)
    tags = [f"热门概念+{best_bonus:.0f}"] if best_bonus > 0 else []
    return best_bonus, tags


def score_valuation(val: Optional[dict]) -> Tuple[float, List[str]]:
    if not val:
        return 0.0, []
    bonus = 0.0
    tags = []
    pe = val.get('pe_ratio')
    pb = val.get('pb_ratio')
    if pe is not None:
        if pe <= 0:
            bonus -= 20
            tags.append("负PE")
        elif pe < 15:
            bonus += 15
            tags.append("低PE")
        elif pe < 30:
            bonus += 5
            tags.append("合理PE")
        elif pe < 60:
            pass
        elif pe < 100:
            bonus -= 10
            tags.append("高PE")
        else:
            bonus -= 20
            tags.append("极高PE")
    if pb is not None:
        if pb <= 0:
            bonus -= 10
            tags.append("负PB")
        elif pb < 2:
            bonus += 10
            tags.append("低PB")
        elif pb < 5:
            bonus += 3
        elif pb < 10:
            pass
        else:
            bonus -= 10
            tags.append("高PB")
    return bonus, tags


def score_fundamentals(fin: Optional[dict]) -> Tuple[float, List[str]]:
    if not fin:
        return 0.0, []
    bonus = 0.0
    tags = []
    net_margin = fin.get('net_margin')
    if net_margin is not None:
        if net_margin > 20:
            bonus += 10
            tags.append("高利润率")
        elif net_margin > 10:
            bonus += 5
        elif net_margin < 0:
            bonus -= 10
            tags.append("亏损")
    gross_margin = fin.get('gross_margin')
    if gross_margin is not None:
        if gross_margin > 40:
            bonus += 8
            tags.append("高毛利")
        elif gross_margin > 20:
            bonus += 3
        elif gross_margin < 10:
            bonus -= 5
            tags.append("低毛利")
    debt_ratio = fin.get('debt_ratio')
    if debt_ratio is not None:
        if debt_ratio < 40:
            bonus += 5
            tags.append("低负债")
        elif debt_ratio > 70:
            bonus -= 10
            tags.append("高负债")
    op_cashflow = fin.get('operating_cashflow')
    net_profit = fin.get('net_profit')
    if op_cashflow is not None and net_profit is not None and net_profit > 0:
        cashflow_ratio = op_cashflow / net_profit
        if cashflow_ratio > 1.2:
            bonus += 8
            tags.append("盈利质量高")
        elif cashflow_ratio < 0.5:
            bonus -= 5
            tags.append("盈利质量低")
    return bonus, tags


def compute_quant_score(
    pct_chg: float,
    turnover: float,
    amount: float,
    is_kc_cy: bool = False,
    amplitude: float = 0,
    volume_ratio: float = 0,
    commission_ratio: float = 0,
    large_order_net: float = 0,
    main_force_net: float = 0,
    strong_ranks: Optional[list] = None,
    earnings_fc: Optional[dict] = None,
    concepts: Optional[list] = None,
    concept_cache: Optional[dict] = None,
    valuation: Optional[dict] = None,
    fundamentals: Optional[dict] = None,
) -> Tuple[float, List[str]]:
    """
    统一量化评分入口。返回 (quant_score, tags)。
    quant_score 范围约 [-50, 200]，调用方负责归一化/封顶。
    """
    total = 0.0
    all_tags = []

    s, t = score_pct_chg(pct_chg, is_kc_cy)
    total += s; all_tags.extend(t)

    s, t = score_turnover(turnover)
    total += s; all_tags.extend(t)

    s, t = score_amount(amount)
    total += s; all_tags.extend(t)

    s, t = score_amplitude(amplitude)
    total += s; all_tags.extend(t)

    s, t = score_volume_ratio(volume_ratio)
    total += s; all_tags.extend(t)

    s, t = score_commission_ratio(commission_ratio)
    total += s; all_tags.extend(t)

    s, t = score_large_order_net(large_order_net)
    total += s; all_tags.extend(t)

    s, t = score_main_force_net(main_force_net)
    total += s; all_tags.extend(t)

    s, t = score_strong_rank(strong_ranks)
    total += s; all_tags.extend(t)

    s, t = score_earnings(earnings_fc)
    total += s; all_tags.extend(t)

    s, t = score_concept(concepts, concept_cache)
    total += s; all_tags.extend(t)

    s, t = score_valuation(valuation)
    total += s; all_tags.extend(t)

    s, t = score_fundamentals(fundamentals)
    total += s; all_tags.extend(t)

    return total, all_tags
