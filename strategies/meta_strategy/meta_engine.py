"""
融合元策略引擎 (Meta-Strategy Engine) v2.0
============================================
七层漏斗编排，融合五大策略优势：

  Layer 0: 大盘风控（漏斗L0）—— 系统性风险门控 + 市场状态识别
  Layer 1: 多因子全市场扫描（多因子扫描器）—— 广度：快速缩小范围 + 行业轮动
  Layer 2: 基本面+流动性过滤（漏斗L1-L2）—— 深度：排除质地差的
  Layer 3: 启动信号识别（主升浪B层）—— 时机：确认启动点（量能+价格+主力+封单）
  Layer 4: 事件驱动加成（LLM多源）—— 催化剂：新闻/研报/公告 + 一票否决
  Layer 5: 隔夜精细评分（八步法核心）—— 精度：最终排序 + 双池分治
  Layer 6: 持续性+卖出（主升浪C/D+八步法止损）—— 闭环：持仓管理

信号链路（严格 PIT）：
  Day T 收盘后(15:10):
    LLM策略 -> 写入 daily_candidates (辅助数据)
    融合引擎 -> 读取全量数据，产出次日候选池
  Day T+1 14:30:
    八步法实时扫描 -> 融合引擎辅助数据 -> 生成买入信号
  Day T+1 ~ T+N:
    持仓管理模块 -> Layer 6 持续性评估 -> 卖出信号

核心原则：
  - 八步法保持核心决策地位（一票否决权）
  - 其他策略是"过滤器"或"加成器"，不做独立筛选
  - 所有信号严格 PIT，无未来函数
  - 市场状态动态调整各层权重
"""
from __future__ import annotations

import json
import logging
import sys
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple

import numpy as np
import pandas as pd
from psycopg2.extras import RealDictCursor

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

from core.db.connection import get_db, close_db_session

logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))


# ============================================================
# 配置
# ============================================================

@dataclass
class MetaStrategyConfig:
    """融合元策略配置 v2.0"""

    # Layer 0: 大盘风控
    layer0_enabled: bool = True
    layer0_min_advancers: int = 2500
    layer0_use_breadth_ema: bool = True
    layer0_partial_cap: float = 0.50
    layer0_regime_lookback: int = 60
    layer0_bull_threshold: float = 0.03
    layer0_bear_threshold: float = -0.05

    # Layer 1: 多因子扫描
    layer1_enabled: bool = True
    layer1_min_total_score: float = 0.30
    layer1_top_n: int = 500
    layer1_momentum_min: float = 0.0
    layer1_rsi_max: float = 75.0
    layer1_macd_bullish: bool = False
    layer1_sar_support: bool = False
    layer1_industry_rotation: bool = True
    layer1_industry_top_n: int = 5

    # Layer 2: 基本面+流动性
    layer2_enabled: bool = True
    layer2_exclude_st: bool = True
    layer2_exclude_new_ipo_days: int = 60
    layer2_min_current_ratio: float = 1.2
    layer2_max_debt_ratio: float = 65.0
    layer2_min_cashflow_ratio: float = 0.5
    layer2_max_goodwill_pct: float = 50.0
    layer2_min_avg_amount_20d: float = 1e8
    layer2_min_circulating_mcap: float = 2e9
    layer2_turn_rate_min: float = 3.0
    layer2_turn_rate_max: float = 15.0

    # Layer 3: 启动信号
    layer3_enabled: bool = True
    layer3_volume_breakout_mult: float = 2.5
    layer3_volume_ma_days: int = 60
    layer3_turnover_min: float = 5.0
    layer3_price_breakout_box_days: int = 60
    layer3_price_above_ma_max_pct: float = 0.08
    layer3_main_force_inflow_min_pct: float = 0.05
    layer3_seal_quality_min: float = 0.6
    layer3_min_launch_score: float = 0.3

    # Layer 4: LLM事件加成
    layer4_enabled: bool = True
    layer4_llm_consensus_bonus: float = 8.0
    layer4_llm_consensus_threshold: float = 60.0
    layer4_llm_finalscore_bonus: float = 10.0
    layer4_llm_finalscore_threshold: float = 30.0
    layer4_llm_mention_bonus: float = 3.0
    layer4_llm_mention_threshold: int = 2
    layer4_llm_selected_bonus: float = 5.0
    layer4_llm_veto_enabled: bool = True
    layer4_llm_veto_keywords: List[str] = field(default_factory=lambda: [
        '暴雷', '退市', '立案调查', '行政处罚', '财务造假',
        '重大诉讼', '违规', '风险警示', 'ST', '*ST',
    ])

    # Layer 5: 八步法精细评分
    layer5_enabled: bool = True
    layer5_min_quant_score: int = 80
    layer5_pct_range_low: float = 2.0
    layer5_pct_range_high: float = 6.0
    layer5_vol_ratio_min: float = 1.5
    layer5_vol_ratio_max: float = 10.0
    layer5_stable_pool_pct_max: float = 5.0
    layer5_upper_pool_pct_min: float = 5.0
    layer5_upper_pool_pct_max: float = 9.5
    layer5_sentiment_enabled: bool = True
    layer5_sentiment_bonus: float = 5.0
    layer5_industry_score_enabled: bool = True
    layer5_industry_score_max: float = 10.0

    # Layer 6: 持续性+卖出
    layer6_enabled: bool = True
    layer6_adx_trend_min: float = 20.0
    layer6_sector_linkage: bool = True
    layer6_consecutive_up_max: int = 7
    layer6_sustain_score_min: float = 0.3
    layer6_hard_stop_loss_pct: float = 0.08
    layer6_overnight_stop_pct: float = 0.025
    layer6_trailing_activate_pct: float = 0.08
    layer6_trailing_stop_pct: float = 0.05
    layer6_max_holding_days: int = 15

    # 动态权重
    weights_bull: Dict[str, float] = field(default_factory=lambda: {
        'factor': 0.35, 'launch': 0.25, 'llm': 0.10, 'overnight': 0.30
    })
    weights_oscillate: Dict[str, float] = field(default_factory=lambda: {
        'factor': 0.25, 'launch': 0.15, 'llm': 0.20, 'overnight': 0.40
    })
    weights_bear: Dict[str, float] = field(default_factory=lambda: {
        'factor': 0.20, 'launch': 0.10, 'llm': 0.25, 'overnight': 0.45
    })

    # 输出
    max_final_candidates: int = 10
    output_dir: str = './results'
    verbose: bool = True

    # 回测
    backtest_start: str = "2025-06-01"
    backtest_end: str = "2026-05-15"
    forward_return_days: List[int] = field(default_factory=lambda: [1, 3, 5, 10, 20])


DEFAULT_META_CONFIG = MetaStrategyConfig()


# ============================================================
# Layer 0: 大盘风控 + 市场状态识别
# ============================================================

def check_market_risk(trade_date: date = None, cfg: MetaStrategyConfig = None) -> Dict:
    """大盘风控检查 + 市场状态识别(牛市/震荡/熊市)"""
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer0_enabled:
        return {
            'passed': True, 'advancers': 0, 'decliners': 0,
            'breadth_ratio': 0, 'breadth_ema': 0, 'position_cap': 1.0,
            'regime': 'oscillate', 'regime_score': 0.0,
            'weights': cfg.weights_oscillate, 'reason': 'Layer0禁用',
        }

    conn = None
    try:
        conn = get_db(use_dict_cursor=True)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        if trade_date is None:
            cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
            row = cur.fetchone()
            trade_date = row['max_date'] if row else datetime.now(BEIJING_TZ).date()

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE pct_chg > 0) as advancers,
                COUNT(*) FILTER (WHERE pct_chg < 0) as decliners,
                COUNT(*) as total
            FROM daily_quotes
            WHERE trade_date = %s;
        """, (trade_date,))
        row = cur.fetchone()
        advancers = int(row['advancers'] or 0)
        decliners = int(row['decliners'] or 0)
        total = int(row['total'] or 1)
        breadth_ratio = advancers / total if total > 0 else 0

        breadth_ema = breadth_ratio
        if cfg.layer0_use_breadth_ema:
            cur.execute("""
                SELECT trade_date,
                    COUNT(*) FILTER (WHERE pct_chg > 0)::float / COUNT(*)::float as breadth
                FROM daily_quotes
                WHERE trade_date >= %s AND trade_date <= %s
                GROUP BY trade_date
                ORDER BY trade_date;
            """, (trade_date - timedelta(days=40), trade_date))
            rows = cur.fetchall()
            if len(rows) >= 5:
                breadths = [float(r['breadth']) for r in rows]
                alpha = 2.0 / 21
                breadth_ema = breadths[0]
                for b in breadths[1:]:
                    breadth_ema = alpha * b + (1 - alpha) * breadth_ema

        # 市场状态识别
        regime = 'oscillate'
        regime_score = 0.0
        cur.execute("""
            SELECT trade_date, close
            FROM daily_quotes
            WHERE ts_code = '000001.SH'
              AND trade_date >= %s AND trade_date <= %s
            ORDER BY trade_date;
        """, (trade_date - timedelta(days=cfg.layer0_regime_lookback + 30), trade_date))
        idx_rows = cur.fetchall()
        if len(idx_rows) >= 20:
            closes = [float(r['close']) for r in idx_rows]
            lookback = min(cfg.layer0_regime_lookback, len(closes) - 1)
            ret = (closes[-1] - closes[-lookback - 1]) / (closes[-lookback - 1] + 1e-9)
            if ret >= cfg.layer0_bull_threshold:
                regime = 'bull'
                regime_score = min(1.0, ret / 0.10)
            elif ret <= cfg.layer0_bear_threshold:
                regime = 'bear'
                regime_score = max(-1.0, ret / 0.10)
            else:
                regime = 'oscillate'
                regime_score = ret / 0.05

        if regime == 'bull':
            weights = cfg.weights_bull
        elif regime == 'bear':
            weights = cfg.weights_bear
        else:
            weights = cfg.weights_oscillate

        cur.close()

        passed = advancers >= cfg.layer0_min_advancers and breadth_ratio >= breadth_ema
        position_cap = 1.0 if passed else cfg.layer0_partial_cap
        if regime == 'bear' and position_cap > 0.3:
            position_cap = 0.3

        return {
            'passed': passed, 'advancers': advancers, 'decliners': decliners,
            'breadth_ratio': round(breadth_ratio, 4), 'breadth_ema': round(breadth_ema, 4),
            'position_cap': position_cap, 'regime': regime,
            'regime_score': round(regime_score, 3), 'weights': weights,
            'reason': '' if passed else f'上涨{advancers}家<{cfg.layer0_min_advancers}或广度低于EMA',
        }
    except Exception as e:
        logger.warning(f"Layer0 大盘风控查询失败: {e}")
        return {
            'passed': True, 'advancers': 0, 'decliners': 0,
            'breadth_ratio': 0, 'breadth_ema': 0, 'position_cap': 1.0,
            'regime': 'oscillate', 'regime_score': 0.0,
            'weights': cfg.weights_oscillate,
            'reason': f'查询失败({e})，默认放行',
        }
    finally:
        pass  # session connection managed by get_db()


# ============================================================
# Layer 1: 多因子全市场扫描 + 行业轮动
# ============================================================

def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    out = np.empty(len(arr), dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def run_multi_factor_scan(trade_date: date, cfg: MetaStrategyConfig = None,
                          stock_pool: List[str] = None) -> pd.DataFrame:
    """多因子扫描 v3.0 — SQL端计算，只返回Top N"""
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer1_enabled:
        return pd.DataFrame()

    conn = None
    try:
        conn = get_db(use_dict_cursor=True)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        start_date = trade_date - timedelta(days=120)

        # 先获取ST/*ST/退市等公认问题股列表，后续排除
        cur.execute("""
            SELECT ts_code FROM stock_basic_info
            WHERE is_st = true OR is_active = false
        """)
        excluded_codes = set(r['ts_code'] for r in cur.fetchall())
        logger.info(f"Layer1: 排除ST/退市股{len(excluded_codes)}只")

        # 先获取当日活跃股票列表（按涨幅优先+成交额保底混合排序）
        # 确保涨停/大涨的中小盘股不被遗漏
        # 排除当日跌幅>5%的股票（防止涨停后大跌日仍被选入，如000030: 3/31涨停→4/1跌-6.47%仍被选）
        # SQL端完成排序和限制，避免传输2400+行到Python
        logger.info(f"Layer1: 查询涨幅前50...")
        cur.execute("""
            SELECT ts_code FROM daily_quotes
            WHERE trade_date = %s AND amount > 50000000 AND pct_chg > -5.0
            ORDER BY pct_chg DESC
            LIMIT 50
        """, (trade_date,))
        pct_top = set(r['ts_code'] for r in cur.fetchall()) - excluded_codes
        logger.info(f"Layer1: 涨幅前50={len(pct_top)}只")

        logger.info(f"Layer1: 查询成交额前50...")
        cur.execute("""
            SELECT ts_code FROM daily_quotes
            WHERE trade_date = %s AND amount > 50000000 AND pct_chg > -5.0
            ORDER BY amount DESC
            LIMIT 50
        """, (trade_date,))
        amount_top = set(r['ts_code'] for r in cur.fetchall()) - excluded_codes
        logger.info(f"Layer1: 成交额前50={len(amount_top)}只")

        # 趋势启动股：当日温和上涨(0-9%)+前5天内有上涨日+成交额>3千万
        # 捕捉早期温和上涨的股票，避免只选当日大涨股导致入场太晚
        # 简化SQL避免窗口函数在大表上超时
        # v10: 放宽涨幅范围(0-9%)和成交额门槛(3千万)，增加LIMIT到50
        logger.info(f"Layer1: 查询趋势启动股...")
        cur.execute("""
            SELECT DISTINCT a.ts_code
            FROM daily_quotes a
            WHERE a.trade_date = %s
              AND a.pct_chg BETWEEN 0 AND 9
              AND a.amount > 30000000
              AND EXISTS (
                SELECT 1 FROM daily_quotes b
                WHERE b.ts_code = a.ts_code
                  AND b.trade_date < %s
                  AND b.trade_date >= %s - INTERVAL '5 days'
                  AND b.pct_chg > 0
              )
            LIMIT 50
        """, (trade_date, trade_date, trade_date))
        trend_top = set(r['ts_code'] for r in cur.fetchall()) - excluded_codes
        logger.info(f"Layer1: 趋势启动={len(trend_top)}只")

        # 均线突破+放量股：收盘>5日均线+成交额>前5日均量1.3倍+成交额>3千万
        # 使用JOIN代替相关子查询，性能更好
        # 加超时保护：如果查询超时就跳过
        ma_break_top = set()
        try:
            logger.info(f"Layer1: 查询均线突破股...")
            cur.execute("""
                SELECT DISTINCT a.ts_code
                FROM daily_quotes a
                JOIN (
                    SELECT ts_code, AVG(close) as ma5, AVG(amount) as avg_amt5
                    FROM daily_quotes
                    WHERE trade_date <= %s AND trade_date > %s - INTERVAL '8 days'
                    GROUP BY ts_code
                    HAVING COUNT(*) >= 3
                ) m ON a.ts_code = m.ts_code
                WHERE a.trade_date = %s
                  AND a.pct_chg > 0
                  AND a.amount > 30000000
                  AND a.close > m.ma5
                  AND a.amount > m.avg_amt5 * 1.3
                LIMIT 40
            """, (trade_date, trade_date, trade_date))
            ma_break_top = set(r['ts_code'] for r in cur.fetchall()) - excluded_codes
        except Exception as e:
            logger.warning(f"Layer1: 均线突破查询失败: {e}")
        logger.info(f"Layer1: 均线突破={len(ma_break_top)}只")

        # 连涨股：近3天均上涨+当日成交额>3千万
        # 使用JOIN+GROUP BY代替EXISTS子查询
        consecutive_top = set()
        try:
            logger.info(f"Layer1: 查询连涨股...")
            cur.execute("""
                SELECT DISTINCT a.ts_code
                FROM daily_quotes a
                JOIN (
                    SELECT ts_code
                    FROM daily_quotes
                    WHERE trade_date < %s AND trade_date >= %s - INTERVAL '4 days'
                      AND pct_chg > 0
                    GROUP BY ts_code
                    HAVING COUNT(*) >= 2
                ) c ON a.ts_code = c.ts_code
                WHERE a.trade_date = %s
                  AND a.pct_chg > 0
                  AND a.amount > 30000000
                LIMIT 30
            """, (trade_date, trade_date, trade_date))
            consecutive_top = set(r['ts_code'] for r in cur.fetchall()) - excluded_codes
        except Exception as e:
            logger.warning(f"Layer1: 连涨股查询失败: {e}")
        logger.info(f"Layer1: 连涨={len(consecutive_top)}只")

        active_codes = list(pct_top | amount_top | trend_top | ma_break_top | consecutive_top)
        logger.info(f"Layer1: 合计{len(active_codes)}只")

        if stock_pool:
            active_codes = [c for c in active_codes if c in stock_pool]

        if not active_codes:
            cur.close()
            return pd.DataFrame()

        # 获取这些股票的历史数据（分批查询避免IN列表过长）
        # batch_size=50，Supabase对单次IN查询50个代码无压力
        batch_size = 50
        all_rows = []
        for batch_start in range(0, len(active_codes), batch_size):
            batch = active_codes[batch_start:batch_start + batch_size]
            placeholders = ','.join(['%s'] * len(batch))
            logger.info(f"Layer1: 查询历史数据 batch {batch_start//batch_size+1} ({len(batch)}只)...")
            cur.execute(f"""
                SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg
                FROM daily_quotes
                WHERE ts_code IN ({placeholders})
                  AND trade_date >= %s AND trade_date <= %s
                ORDER BY ts_code, trade_date;
            """, batch + [start_date, trade_date])
            rows = cur.fetchall()
            logger.info(f"Layer1: 历史数据 {len(rows)}行")
            all_rows.extend(rows)
            # 批次间短暂延迟，避免Supabase连接池压力
            if batch_start + batch_size < len(active_codes):
                import time; time.sleep(0.1)

        cur.close()
        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(all_rows)
        for c in ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']:
            df[c] = pd.to_numeric(df[c], errors='coerce')

        results = []
        for ts_code, group in df.groupby('ts_code'):
            group = group.sort_values('trade_date').tail(60)
            if len(group) < 30:
                continue
            close = group['close'].values.astype(float)
            high = group['high'].values.astype(float)
            low = group['low'].values.astype(float)
            amount = group['amount'].values.astype(float)
            n = len(close)
            if close[-1] <= 0:
                continue

            r = {'ts_code': ts_code, 'close': round(float(close[-1]), 2)}

            # 1. 动量
            skip, win = 1, 20
            if n >= win + skip + 1:
                mom = float((close[-(skip+1)] - close[-(win+skip+1)]) / (close[-(win+skip+1)] + 1e-9))
            else:
                mom = 0.0
            r['momentum'] = round(mom, 4)
            r['momentum_score'] = round(min(max(mom / 0.15, 0), 1.0) if mom > 0 else 0.0, 3)

            # 2. 量比
            vw = 20
            vol_mean = amount[-(vw+1):-1].mean() if n >= vw + 1 else amount.mean()
            vr = float(amount[-1]) / (float(vol_mean) + 1e-9)
            r['volume_ratio'] = round(vr, 2)
            cw = 10
            if n >= cw:
                cp = close[-cw:] - close[-cw:].mean()
                cv = amount[-cw:] - amount[-cw:].mean()
                pv_corr = float((cp * cv).sum() / (np.sqrt((cp**2).sum() * (cv**2).sum()) + 1e-9))
            else:
                pv_corr = 0.0
            r['pv_corr'] = round(pv_corr, 3)
            r['volume_score'] = round(
                (min((vr - 1.5) / 8.5, 1.0) * 0.7 + min(pv_corr, 1.0) * 0.3)
                if 1.5 <= vr <= 10.0 and pv_corr > 0 else 0.0, 3)

            # 3. RSI
            d = np.diff(close)
            ag = _ema(np.where(d > 0, d, 0.0), 6)
            al = _ema(np.where(d < 0, -d, 0.0), 6)
            rsi = float(100 - 100 / (1 + ag[-1] / (al[-1] + 1e-9)))
            r['rsi'] = round(rsi, 1)
            r['rsi_score'] = (0.0 if rsi >= 75 else 1.0 if rsi <= 35 else round((75 - rsi) / 40, 3))

            # 4. MACD
            dif = _ema(close, 12) - _ema(close, 26)
            dea = _ema(dif, 9)
            hist = (dif - dea) * 2
            ld, la = float(dif[-1]), float(dea[-1])
            lh, ph = float(hist[-1]), float(hist[-2]) if n > 1 else 0.0
            r['macd_dif'] = round(ld, 4)
            r['macd_dea'] = round(la, 4)
            if ld > la and ph <= 0 and lh > 0:
                r['macd_score'] = 1.0; r['macd_signal'] = '金叉'
            elif ld > la and lh > 0 and lh > ph:
                r['macd_score'] = 0.7; r['macd_signal'] = '多头'
            elif ld > la:
                r['macd_score'] = 0.4; r['macd_signal'] = 'DIF>DEA'
            else:
                r['macd_score'] = 0.1; r['macd_signal'] = '空头'

            # 5. EMA
            e5 = float(_ema(close, 5)[-1])
            e10 = float(_ema(close, 10)[-1])
            e20 = float(_ema(close, 20)[-1])
            last_close = float(close[-1])
            if last_close > e5 > e10 > e20:
                r['ema_score'] = 1.0; r['ema_signal'] = '完美多头'
            elif last_close > e10 > e20:
                r['ema_score'] = 0.7; r['ema_signal'] = '中期多头'
            elif last_close > e20:
                r['ema_score'] = 0.4; r['ema_signal'] = '站上均线'
            elif e5 > e10:
                r['ema_score'] = 0.3; r['ema_signal'] = '短期金叉'
            else:
                r['ema_score'] = 0.0; r['ema_signal'] = '空头排列'

            # 6. ADX
            p = 14
            tr = np.maximum(high[1:] - low[1:],
                            np.maximum(np.abs(high[1:] - close[:-1]),
                                       np.abs(low[1:] - close[:-1])))
            atr = _ema(tr, p)
            up = high[1:] - high[:-1]; dn = low[:-1] - low[1:]
            pdi = 100 * _ema(np.where((up > dn) & (up > 0), up, 0.0), p) / (atr + 1e-9)
            mdi = 100 * _ema(np.where((dn > up) & (dn > 0), dn, 0.0), p) / (atr + 1e-9)
            adx_v = float(_ema(100 * np.abs(pdi - mdi) / (pdi + mdi + 1e-9), p)[-1])
            lpdi, lmdi = float(pdi[-1]), float(mdi[-1])
            r['plus_di'] = round(lpdi, 1)
            r['minus_di'] = round(lmdi, 1)
            r['adx'] = round(adx_v, 1)
            if adx_v >= 20 and lpdi > lmdi:
                r['adx_score'] = round(min(adx_v / 50, 1.0), 3)
                r['adx_signal'] = f'强趋势(ADX={adx_v:.0f})'
            elif lpdi > lmdi:
                r['adx_score'] = 0.3; r['adx_signal'] = f'弱趋势(ADX={adx_v:.0f})'
            else:
                r['adx_score'] = 0.0; r['adx_signal'] = f'偏空(ADX={adx_v:.0f})'

            # 7. SAR
            af_i, af_m = 0.02, 0.2
            sar = np.zeros(n); trend = 1; ep = high[0]; af = af_i; sar[0] = low[0]
            for i in range(1, n):
                ps = sar[i-1]
                if trend == 1:
                    sar[i] = min(ps + af * (ep - ps), low[i-1], low[max(0, i-2)])
                    if low[i] < sar[i]:
                        trend = -1; sar[i] = ep; ep = low[i]; af = af_i
                    elif high[i] > ep:
                        ep = high[i]; af = min(af + af_i, af_m)
                else:
                    sar[i] = max(ps + af * (ep - ps), high[i-1], high[max(0, i-2)])
                    if high[i] > sar[i]:
                        trend = 1; sar[i] = ep; ep = high[i]; af = af_i
                    elif low[i] < ep:
                        ep = low[i]; af = min(af + af_i, af_m)
            ls = float(sar[-1])
            r['sar'] = round(ls, 2)
            if ls < last_close:
                sd = (last_close - ls) / last_close
                r['sar_score'] = round(min(1.0, max(0.3, 1.0 - sd * 10)), 3)
                r['sar_signal'] = f'SAR支撑({sd*100:.1f}%)'
            else:
                r['sar_score'] = 0.0; r['sar_signal'] = 'SAR压制'

            # 8. 行业轮动因子 (v2.0)
            if n >= 6:
                stock_5d_ret = (close[-1] - close[-6]) / (close[-6] + 1e-9)
                r['industry_rotation_score'] = round(
                    min(max(stock_5d_ret / 0.10, 0), 1.0) if stock_5d_ret > 0 else 0.0, 3)
            else:
                r['industry_rotation_score'] = 0.0

            # 加权总分 (v2.0: 行业轮动占10%)
            r['total_score'] = round(
                r['momentum_score'] * 0.18 + r['volume_score'] * 0.18 +
                r['rsi_score'] * 0.13 + r['macd_score'] * 0.13 +
                r['ema_score'] * 0.13 + r['adx_score'] * 0.10 +
                r['sar_score'] * 0.05 + r['industry_rotation_score'] * 0.10, 4)

            results.append(r)

        if not results:
            return pd.DataFrame()

        df_result = pd.DataFrame(results)
        mask = pd.Series(True, index=df_result.index)
        if cfg.layer1_momentum_min is not None:
            mask &= df_result['momentum'] >= cfg.layer1_momentum_min
        if cfg.layer1_rsi_max is not None:
            mask &= df_result['rsi'] < cfg.layer1_rsi_max
        if cfg.layer1_macd_bullish:
            mask &= df_result['macd_dif'] > df_result['macd_dea']
        if cfg.layer1_sar_support:
            mask &= df_result['sar'] < df_result['close']
        mask &= df_result['total_score'] >= cfg.layer1_min_total_score

        df_filtered = df_result[mask].sort_values('total_score', ascending=False)
        if len(df_filtered) > cfg.layer1_top_n:
            df_filtered = df_filtered.head(cfg.layer1_top_n)
        return df_filtered.reset_index(drop=True)

    except Exception as e:
        logger.error(f"Layer1 多因子扫描失败: {e}")
        import traceback; traceback.print_exc()
        return pd.DataFrame()
    finally:
        pass  # session connection managed by get_db()



# ============================================================
# Layer 2: 基本面+流动性过滤
# ============================================================

def run_fundamental_liquidity_filter(stock_list: List[str], trade_date: date,
                                     cfg: MetaStrategyConfig = None) -> List[str]:
    """基本面防雷 + 流动性过滤（含减持/质押/商誉）"""
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer2_enabled:
        return stock_list

    conn = None
    try:
        conn = get_db(use_dict_cursor=True)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT DISTINCT ON (ts_code) ts_code, report_date,
                   revenue, net_profit, gross_margin, net_margin,
                   total_assets, total_liabilities, current_assets, current_liabilities,
                   debt_ratio, equity, operating_cashflow, accounts_receivable, inventory,
                   goodwill, pledge_ratio, revenue_yoy, profit_yoy, industry, listing_date
            FROM stock_fundamentals WHERE report_date <= %s
            ORDER BY ts_code, report_date DESC;
        """, (trade_date,))
        fin_cache = {}
        for r in cur.fetchall():
            fin_cache[r['ts_code']] = {}
            for k, v in r.items():
                if k == 'ts_code':
                    continue
                if v is None:
                    fin_cache[r['ts_code']][k] = None
                elif isinstance(v, (int, float)):
                    fin_cache[r['ts_code']][k] = float(v)
                elif isinstance(v, (date, datetime)):
                    fin_cache[r['ts_code']][k] = v
                else:
                    try:
                        fin_cache[r['ts_code']][k] = float(v)
                    except (ValueError, TypeError):
                        fin_cache[r['ts_code']][k] = v

        cur.execute("""
            SELECT ts_code, stock_name, list_date, is_st
            FROM stock_basic_info WHERE ts_code = ANY(%s);
        """, (stock_list,))
        st_cache = {}
        for r in cur.fetchall():
            st_cache[r['ts_code']] = {
                'stock_name': r['stock_name'] or '',
                'list_date': r['list_date'],
                'is_st': r['is_st'] or False,
            }

        start_20d = trade_date - timedelta(days=40)
        cur.execute("""
            SELECT ts_code, amount, turnover_rate, circulating_market_cap, total_market_cap
            FROM daily_quotes WHERE trade_date = %s AND ts_code = ANY(%s);
        """, (trade_date, stock_list))
        liq_cache = {}
        for r in cur.fetchall():
            liq_cache[r['ts_code']] = {
                'amount': float(r['amount']) if r['amount'] else 0,
                'turnover_rate': float(r['turnover_rate']) if r['turnover_rate'] else 0,
                'circulating_market_cap': float(r['circulating_market_cap']) if r['circulating_market_cap'] else 0,
                'total_market_cap': float(r['total_market_cap']) if r['total_market_cap'] else 0,
            }

        cur.execute("""
            SELECT ts_code, amount FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s AND ts_code = ANY(%s)
            ORDER BY ts_code, trade_date DESC;
        """, (start_20d, trade_date, stock_list))
        rows_20d = cur.fetchall()
        df_20d = pd.DataFrame(rows_20d)
        avg_amount_cache = {}
        if not df_20d.empty:
            df_20d['amount'] = pd.to_numeric(df_20d['amount'], errors='coerce')
            for ts_code, grp in df_20d.groupby('ts_code'):
                amounts = grp['amount'].dropna().head(20)
                if len(amounts) >= 1:
                    avg_amount_cache[ts_code] = float(amounts.mean())

        reduction_codes = set()
        try:
            cur.execute("""
                SELECT DISTINCT ts_code FROM stock_announcements
                WHERE publish_date >= %s
                  AND (title ILIKE '%%减持%%' OR title ILIKE '%%reduce%%')
                  AND ts_code = ANY(%s);
            """, (trade_date - timedelta(days=60), stock_list))
            for r in cur.fetchall():
                reduction_codes.add(r['ts_code'])
        except Exception:
            logger.debug("减持公告表不存在，跳过")

        cur.close()

        passed = []
        reject_stats = {}
        for ts_code in stock_list:
            reason = _check_fundamental_liquidity(
                ts_code,
                st_cache.get(ts_code, {}),
                fin_cache.get(ts_code, {}),
                liq_cache.get(ts_code, {}),
                avg_amount_cache.get(ts_code, 0),
                reduction_codes,
                trade_date,
                cfg,
            )
            if reason is None:
                passed.append(ts_code)
            else:
                reject_stats[reason] = reject_stats.get(reason, 0) + 1
        if cfg.verbose and reject_stats:
            logger.info(f"  Layer2 淘汰: {dict(reject_stats)}")
        return passed
    except Exception as e:
        logger.warning(f"Layer2 基本面过滤失败: {e}，返回原始列表")
        return stock_list
    finally:
        pass  # session connection managed by get_db()


def _check_fundamental_liquidity(ts_code, stock_info, fin, liq,
                                  avg_amount, reduction_codes, trade_date, cfg) -> Optional[str]:
    """单只股票基本面+流动性检查，返回拒绝原因或None"""
    if cfg.layer2_exclude_st and (stock_info.get('is_st') or 'ST' in stock_info.get('stock_name', '')):
        return 'ST'
    if cfg.layer2_exclude_new_ipo_days > 0 and stock_info.get('list_date'):
        ld = stock_info['list_date']
        if isinstance(ld, str):
            try:
                ld = date.fromisoformat(ld)
            except ValueError:
                ld = None
        if ld and (trade_date - ld).days < cfg.layer2_exclude_new_ipo_days:
            return '次新股'
    if avg_amount < cfg.layer2_min_avg_amount_20d:
        return '成交额不足'
    turn = liq.get('turnover_rate', 0)
    if turn > 0 and (turn < cfg.layer2_turn_rate_min or turn > cfg.layer2_turn_rate_max):
        return '换手不符'
    circ_mcap = liq.get('circulating_market_cap', 0) or liq.get('total_market_cap', 0)
    if circ_mcap > 0 and circ_mcap < cfg.layer2_min_circulating_mcap:
        return '市值不足'
    if not fin:
        return None
    if fin.get('net_profit') is not None and fin['net_profit'] <= 0:
        return '亏损'
    if fin.get('debt_ratio') is not None and fin['debt_ratio'] > cfg.layer2_max_debt_ratio:
        return '负债率高'
    if (fin.get('operating_cashflow') is not None
            and fin.get('net_profit') is not None
            and fin['net_profit'] > 0):
        if fin['operating_cashflow'] / fin['net_profit'] < cfg.layer2_min_cashflow_ratio:
            return '现金流差'
    if (fin.get('goodwill') is not None
            and fin.get('equity') is not None
            and fin['equity'] > 0):
        if fin['goodwill'] / fin['equity'] * 100 > cfg.layer2_max_goodwill_pct:
            return '商誉风险'
    if ts_code in reduction_codes:
        return '减持'
    if fin.get('pledge_ratio') is not None and fin['pledge_ratio'] > 50.0:
        return '高质押'
    return None


# ============================================================
# Layer 3: 启动信号识别（完整主升浪B层逻辑）
# ============================================================

def detect_launch_signals(stock_list: List[str], trade_date: date,
                          cfg: MetaStrategyConfig = None) -> pd.DataFrame:
    """启动信号检测 v2.0（量能突破+价格突破+主力资金+封单质量）"""
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer3_enabled:
        return pd.DataFrame({'ts_code': stock_list, 'launch_score': 1.0})

    conn = None
    try:
        conn = get_db(use_dict_cursor=True)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        start_date = trade_date - timedelta(days=120)
        cur.execute("""
            SELECT ts_code, trade_date, open, high, low, close,
                   volume, amount, pct_chg, turnover_rate
            FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s AND ts_code = ANY(%s)
            ORDER BY ts_code, trade_date;
        """, (start_date, trade_date, stock_list))
        rows = cur.fetchall()
        cur.close()
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        for c in ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg', 'turnover_rate']:
            df[c] = pd.to_numeric(df[c], errors='coerce')

        results = []
        for ts_code, group in df.groupby('ts_code'):
            group = group.sort_values('trade_date')
            if len(group) < 30:
                continue
            close = group['close'].values
            high = group['high'].values
            low = group['low'].values
            amount = group['amount'].values
            turnover = group['turnover_rate'].values
            pct_chg_vals = group['pct_chg'].values
            n = len(group)
            r = {'ts_code': ts_code}
            score = 0.0

            # 维度1: 量能突破
            vol_ma60 = amount[-61:-1].mean() if n >= 60 else (
                amount[:-1].mean() if n > 1 else amount[0])
            vol_ratio = amount[-1] / (vol_ma60 + 1e-9)
            if vol_ratio >= cfg.layer3_volume_breakout_mult:
                score += 0.30
                r['volume_breakout'] = True
            elif vol_ratio >= cfg.layer3_volume_breakout_mult * 0.7:
                score += 0.15
                r['volume_breakout'] = False
            else:
                r['volume_breakout'] = False
            r['vol_ratio'] = round(vol_ratio, 2)

            last_turnover = float(turnover[-1]) if not np.isnan(turnover[-1]) else 0
            if last_turnover >= cfg.layer3_turnover_min:
                score += 0.05
                r['turnover_ok'] = True
            else:
                r['turnover_ok'] = False
            r['turnover'] = round(last_turnover, 2)

            # 维度2: 价格突破
            if n >= cfg.layer3_price_breakout_box_days:
                box_high = high[-(cfg.layer3_price_breakout_box_days+1):-1].max()
                if close[-1] > box_high:
                    score += 0.25
                    r['price_breakout'] = True
                    r['box_high'] = round(float(box_high), 2)
                elif close[-1] > box_high * 0.98:
                    score += 0.10
                    r['price_breakout'] = False
                else:
                    r['price_breakout'] = False
            else:
                r['price_breakout'] = False

            if n >= 20:
                ma20 = close[-21:-1].mean()
                pct_above_ma = (close[-1] - ma20) / (ma20 + 1e-9)
                if 0 < pct_above_ma < cfg.layer3_price_above_ma_max_pct:
                    score += 0.05
                    r['near_ma'] = True
                    r['pct_above_ma20'] = round(pct_above_ma, 4)
                else:
                    r['near_ma'] = False

            # 维度3: 主力资金（代理指标）
            last_pct = float(pct_chg_vals[-1]) if not np.isnan(pct_chg_vals[-1]) else 0
            if last_turnover > 5 and last_pct > 0 and amount[-1] > vol_ma60 * 1.5:
                main_force_score = min(1.0, (last_pct / 5.0) * (last_turnover / 8.0))
                if main_force_score >= cfg.layer3_main_force_inflow_min_pct:
                    score += 0.20
                    r['main_force'] = True
                else:
                    r['main_force'] = False
                r['main_force_score'] = round(main_force_score, 3)
            else:
                r['main_force'] = False
                r['main_force_score'] = 0.0

            # 维度4: 封单质量
            seal_quality = 0.0
            if 7.0 <= last_pct < 9.8:
                seal_quality = (0.8 if 3.0 <= last_turnover <= 15.0
                                else (0.5 if last_turnover > 15.0 else 0.3))
            elif last_pct >= 9.8:
                seal_quality = (1.0 if last_turnover < 5.0
                                else (0.9 if last_turnover < 10.0 else 0.6))
            r['seal_quality'] = round(seal_quality, 2)
            if seal_quality >= cfg.layer3_seal_quality_min:
                score += 0.15
                r['seal_ok'] = True
            else:
                r['seal_ok'] = False

            r['pct_chg'] = round(last_pct, 2)
            r['launch_score'] = round(min(score, 1.0), 3)
            results.append(r)

        if not results:
            return pd.DataFrame()
        df_result = pd.DataFrame(results)
        df_result = df_result[df_result['launch_score'] >= cfg.layer3_min_launch_score]
        return df_result.reset_index(drop=True)
    except Exception as e:
        logger.error(f"Layer3 启动信号检测失败: {e}")
        return pd.DataFrame()
    finally:
        pass  # session connection managed by get_db()



# ============================================================
# Layer 4: LLM事件驱动加成 + 一票否决
# ============================================================

def get_llm_boost(stock_list: List[str], trade_date: date,
                  cfg: MetaStrategyConfig = None) -> Dict[str, Dict]:
    """LLM多源策略辅助数据 v2.0（含一票否决机制）"""
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer4_enabled:
        return {c: {'llm_score': 0, 'llm_bonus': 0, 'sources': [],
                     'llm_veto': False, 'veto_reason': ''}
                for c in stock_list}

    conn = None
    try:
        conn = get_db(use_dict_cursor=True)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT ts_code, stock_name, final_score, llm_score,
                   quant_score, source_diversity, logic_tags
            FROM daily_candidates
            WHERE snapshot_date = (
                SELECT MAX(snapshot_date) FROM daily_candidates
                WHERE snapshot_date <= %s AND source = 'llm_multisource'
            )
            AND source = 'llm_multisource' AND selected = TRUE
            AND ts_code = ANY(%s);
        """, (trade_date, stock_list))

        llm_data = {}
        for r in cur.fetchall():
            ts_code = r['ts_code']
            bonus = 0.0
            sources = []
            final_score = float(r['final_score'] or 0)
            llm_score = float(r['llm_score'] or 0)
            diversity = int(r['source_diversity'] or 0)
            logic_tags = r.get('logic_tags', '') or ''

            if final_score >= cfg.layer4_llm_finalscore_threshold:
                bonus += cfg.layer4_llm_finalscore_bonus
                sources.append(f'LLM评分{final_score:.0f}')
            if llm_score >= cfg.layer4_llm_consensus_threshold:
                bonus += cfg.layer4_llm_consensus_bonus
                sources.append(f'共识{llm_score:.0f}')
            if diversity >= cfg.layer4_llm_mention_threshold:
                bonus += cfg.layer4_llm_mention_bonus
                sources.append(f'{diversity}源提及')
            bonus += cfg.layer4_llm_selected_bonus
            sources.append('LLM选中')

            # 一票否决
            llm_veto = False
            veto_reason = ''
            if cfg.layer4_llm_veto_enabled:
                tags_lower = logic_tags.lower() if isinstance(logic_tags, str) else ''
                stock_name = r.get('stock_name', '') or ''
                for kw in cfg.layer4_llm_veto_keywords:
                    if kw.lower() in tags_lower or kw in stock_name:
                        llm_veto = True
                        veto_reason = f'LLM否决: 含关键词"{kw}"'
                        bonus = 0
                        sources = [veto_reason]
                        break

            llm_data[ts_code] = {
                'llm_score': llm_score,
                'final_score': final_score,
                'source_diversity': diversity,
                'llm_bonus': round(bonus, 1),
                'sources': sources,
                'llm_veto': llm_veto,
                'veto_reason': veto_reason,
            }

        cur.close()
        for c in stock_list:
            if c not in llm_data:
                llm_data[c] = {
                    'llm_score': 0, 'final_score': 0, 'source_diversity': 0,
                    'llm_bonus': 0, 'sources': [], 'llm_veto': False, 'veto_reason': '',
                }
        return llm_data
    except Exception as e:
        logger.warning(f"Layer4 LLM数据加载失败: {e}")
        return {c: {'llm_score': 0, 'llm_bonus': 0, 'sources': [],
                     'llm_veto': False, 'veto_reason': ''}
                for c in stock_list}
    finally:
        pass  # session connection managed by get_db()


# ============================================================
# Layer 5: 八步法精细评分（双池分治 + 情绪感知 + 行业评分）
# ============================================================

def compute_overnight_score(stock_list: List[str], trade_date: date,
                            cfg: MetaStrategyConfig = None) -> pd.DataFrame:
    """八步法核心评分 v2.0（双池分治+情绪感知+行业评分）"""
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer5_enabled:
        return pd.DataFrame({'ts_code': stock_list, 'overnight_score': 80, 'pool': 'stable'})

    conn = None
    try:
        conn = get_db(use_dict_cursor=True)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT ts_code, trade_date, open, high, low, close,
                   volume, amount, pct_chg, turnover_rate
            FROM daily_quotes
            WHERE trade_date = %s AND ts_code = ANY(%s);
        """, (trade_date, stock_list))
        rows = cur.fetchall()

        start_20d = trade_date - timedelta(days=40)
        cur.execute("""
            SELECT ts_code, amount FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s AND ts_code = ANY(%s)
            ORDER BY ts_code, trade_date DESC;
        """, (start_20d, trade_date, stock_list))
        rows_20d = cur.fetchall()
        df_20d = pd.DataFrame(rows_20d)
        avg_amount_cache = {}
        if not df_20d.empty:
            df_20d['amount'] = pd.to_numeric(df_20d['amount'], errors='coerce')
            for ts_code, grp in df_20d.groupby('ts_code'):
                amounts = grp['amount'].dropna().head(20)
                if len(amounts) >= 1:
                    avg_amount_cache[ts_code] = float(amounts.mean())

        start_5d = trade_date - timedelta(days=15)
        cur.execute("""
            SELECT ts_code, trade_date, close FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s AND ts_code = ANY(%s)
            ORDER BY ts_code, trade_date;
        """, (start_5d, trade_date, stock_list))
        rows_5d = cur.fetchall()
        df_5d = pd.DataFrame(rows_5d)
        ma5_cache = {}
        if not df_5d.empty:
            df_5d['close'] = pd.to_numeric(df_5d['close'], errors='coerce')
            for ts_code, grp in df_5d.groupby('ts_code'):
                closes = grp['close'].dropna().tail(6)
                if len(closes) >= 5:
                    ma5_cache[ts_code] = float(closes.iloc[:-1].tail(5).mean())

        # 情绪感知
        sentiment_score = 0.0
        if cfg.layer5_sentiment_enabled:
            cur.execute("""
                SELECT COUNT(*) FILTER (WHERE pct_chg > 0) as advancers,
                       COUNT(*) FILTER (WHERE pct_chg > 5) as strong_advancers,
                       COUNT(*) as total
                FROM daily_quotes WHERE trade_date = %s;
            """, (trade_date,))
            sent_row = cur.fetchone()
            if sent_row:
                adv_ratio = float(sent_row['advancers'] or 0) / max(int(sent_row['total'] or 1), 1)
                strong_ratio = float(sent_row['strong_advancers'] or 0) / max(int(sent_row['total'] or 1), 1)
                if adv_ratio > 0.6 and strong_ratio > 0.05:
                    sentiment_score = cfg.layer5_sentiment_bonus
                elif adv_ratio > 0.5:
                    sentiment_score = cfg.layer5_sentiment_bonus * 0.5

        # 行业评分
        industry_map = {}
        industry_score_cache = {}
        if cfg.layer5_industry_score_enabled:
            cur.execute("""
                SELECT ts_code, industry FROM stock_fundamentals
                WHERE report_date <= %s AND ts_code = ANY(%s)
                ORDER BY ts_code, report_date DESC;
            """, (trade_date, stock_list))
            for r in cur.fetchall():
                if r['ts_code'] not in industry_map and r['industry']:
                    industry_map[r['ts_code']] = r['industry']
            if industry_map:
                cur.execute("SELECT ts_code, pct_chg FROM daily_quotes WHERE trade_date = %s;", (trade_date,))
                all_pct = cur.fetchall()
                industry_pcts = {}
                for r in all_pct:
                    ind = industry_map.get(r['ts_code'])
                    if ind and r['pct_chg'] is not None:
                        industry_pcts.setdefault(ind, []).append(float(r['pct_chg']))
                industry_avg = {ind: np.mean(pcts) for ind, pcts in industry_pcts.items() if pcts}
                sorted_industries = sorted(industry_avg.items(), key=lambda x: x[1], reverse=True)
                for rank, (ind, avg_pct) in enumerate(sorted_industries):
                    if rank < cfg.layer1_industry_top_n:
                        industry_score_cache[ind] = round(
                            cfg.layer5_industry_score_max * (1 - rank / cfg.layer1_industry_top_n), 1)
                    else:
                        industry_score_cache[ind] = 0

        cur.close()
        if not rows:
            return pd.DataFrame()

        results = []
        for r in rows:
            ts_code = r['ts_code']
            pct_chg = float(r['pct_chg'] or 0)
            turnover = float(r['turnover_rate'] or 0)
            amount_val = float(r['amount'] or 0)
            close_val = float(r['close'] or 0)
            if close_val <= 0:
                continue

            score = 0.0
            tags = []

            # 双池分类
            if pct_chg <= cfg.layer5_stable_pool_pct_max:
                pool = 'stable'
            elif pct_chg <= cfg.layer5_upper_pool_pct_max:
                pool = 'upper'
            else:
                pool = 'extreme'

            # 涨幅评分（双池差异化）
            if pool == 'stable':
                if 2.0 <= pct_chg <= 4.0:
                    score += 30; tags.append('稳健蓄势')
                elif 4.0 < pct_chg <= 5.0:
                    score += 25; tags.append('蓄势突破')
                elif 0 < pct_chg < 2.0:
                    score += 15
            elif pool == 'upper':
                if 5.0 < pct_chg <= 7.0:
                    score += 22; tags.append('强势突破')
                elif 7.0 < pct_chg <= 9.5:
                    score += 12; tags.append('追涨风险')

            if turnover > 0:
                score += min(30, turnover * 2)
            if 5.0 <= turnover <= 8.0:
                tags.append('黄金换手')
            if amount_val > 0:
                score += min(40, 10 * np.log10(amount_val / 1e8 + 1))

            avg_amt = avg_amount_cache.get(ts_code, 0)
            vol_ratio = 0.0
            if avg_amt > 0:
                vol_ratio = amount_val / avg_amt
                if vol_ratio >= 1.5:
                    score += min(15, (vol_ratio - 1.5) * 10)
                    tags.append(f'量比{vol_ratio:.1f}')

            ma5 = ma5_cache.get(ts_code)
            if ma5 and ma5 > 0:
                bias = (close_val - ma5) / ma5
                if 0 < bias < 0.02:
                    score += 10; tags.append('贴线')
                elif 0.02 <= bias < 0.05:
                    score += 5

            score += sentiment_score
            if sentiment_score > 0:
                tags.append('情绪好')

            ind = industry_map.get(ts_code, '') if cfg.layer5_industry_score_enabled else ''
            ind_score = industry_score_cache.get(ind, 0)
            score += ind_score
            if ind_score > 0:
                tags.append('热门行业')

            if pool == 'upper' and pct_chg > 8.0:
                score -= 10; tags.append('高位风险')
            elif pool == 'extreme':
                score -= 20; tags.append('极端高位')

            results.append({
                'ts_code': ts_code,
                'overnight_score': round(score, 1),
                'pool': pool,
                'pct_chg': round(pct_chg, 2),
                'turnover': round(turnover, 2),
                'amount': round(amount_val, 0),
                'vol_ratio': round(vol_ratio, 2),
                'sentiment_score': round(sentiment_score, 1),
                'industry_score': round(ind_score, 1),
                'tags': tags,
            })

        if not results:
            return pd.DataFrame()
        return pd.DataFrame(results)
    except Exception as e:
        logger.error(f"Layer5 八步法评分失败: {e}")
        return pd.DataFrame()
    finally:
        pass  # session connection managed by get_db()



# ============================================================
# Layer 6: 持续性评估 + 卖出信号
# ============================================================

def evaluate_sustainability(stock_list: List[str], trade_date: date,
                            cfg: MetaStrategyConfig = None) -> pd.DataFrame:
    """持续性评估 v2.0（主升浪C层+八步法止损）"""
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer6_enabled:
        return pd.DataFrame({'ts_code': stock_list, 'sustain_score': 0.5, 'exit_signal': ''})

    conn = None
    try:
        conn = get_db(use_dict_cursor=True)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        start_date = trade_date - timedelta(days=120)
        cur.execute("""
            SELECT ts_code, trade_date, open, high, low, close,
                   volume, amount, pct_chg, turnover_rate
            FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s AND ts_code = ANY(%s)
            ORDER BY ts_code, trade_date;
        """, (start_date, trade_date, stock_list))
        rows = cur.fetchall()
        cur.close()
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        for c in ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg', 'turnover_rate']:
            df[c] = pd.to_numeric(df[c], errors='coerce')

        results = []
        for ts_code, group in df.groupby('ts_code'):
            group = group.sort_values('trade_date')
            if len(group) < 30:
                continue
            close = group['close'].values.astype(float)
            high = group['high'].values.astype(float)
            low = group['low'].values.astype(float)
            amount = group['amount'].values.astype(float)
            pct_chg_vals = group['pct_chg'].values.astype(float)
            n = len(close)
            if close[-1] <= 0:
                continue

            r = {'ts_code': ts_code}
            sustain_score = 0.0
            exit_signals = []

            # 维度1: ADX趋势强度
            p = 14
            tr = np.maximum(high[1:] - low[1:],
                            np.maximum(np.abs(high[1:] - close[:-1]),
                                       np.abs(low[1:] - close[:-1])))
            up = high[1:] - high[:-1]
            dn = low[:-1] - low[1:]
            atr = _ema(tr, p)
            pdi = 100 * _ema(np.where((up > dn) & (up > 0), up, 0.0), p) / (atr + 1e-9)
            mdi = 100 * _ema(np.where((dn > up) & (dn > 0), dn, 0.0), p) / (atr + 1e-9)
            adx_arr = _ema(100 * np.abs(pdi - mdi) / (pdi + mdi + 1e-9), p)
            adx_val = float(adx_arr[-1])
            pdi_val = float(pdi[-1])
            mdi_val = float(mdi[-1])
            r['adx'] = round(adx_val, 1)
            r['plus_di'] = round(pdi_val, 1)
            r['minus_di'] = round(mdi_val, 1)

            if adx_val >= cfg.layer6_adx_trend_min and pdi_val > mdi_val:
                sustain_score += 0.30
                r['adx_signal'] = '趋势持续'
            elif pdi_val > mdi_val:
                sustain_score += 0.15
                r['adx_signal'] = '弱趋势'
            else:
                sustain_score += 0.0
                r['adx_signal'] = '趋势反转'
                exit_signals.append('ADX趋势反转')

            if len(adx_arr) >= 5:
                adx_drop = float(adx_arr[-5]) - adx_val
                if adx_drop >= 10:
                    sustain_score -= 0.10
                    r['adx_decay'] = True
                    exit_signals.append('ADX衰减')

            # 维度2: 板块联动
            if cfg.layer6_sector_linkage and n >= 6:
                stock_5d_ret = (close[-1] - close[-6]) / (close[-6] + 1e-9)
                if stock_5d_ret > 0.03:
                    sustain_score += 0.15
                    r['sector_signal'] = '板块强势'
                elif stock_5d_ret > 0:
                    sustain_score += 0.08
                    r['sector_signal'] = '板块偏强'
                else:
                    sustain_score += 0.0
                    r['sector_signal'] = '板块弱势'
                    exit_signals.append('板块走弱')

            # 维度3: 连涨天数
            consecutive_up = 0
            for i in range(-1, -min(n, 15), -1):
                if pct_chg_vals[i] > 0:
                    consecutive_up += 1
                else:
                    break
            r['consecutive_up'] = consecutive_up
            if consecutive_up <= 3:
                sustain_score += 0.15
            elif consecutive_up <= cfg.layer6_consecutive_up_max:
                sustain_score += 0.08
            else:
                sustain_score -= 0.10
                exit_signals.append(f'连涨{consecutive_up}天过热')

            # 维度4: 量能配合
            if n >= 10:
                up_mask = pct_chg_vals[-10:] > 0
                down_mask = pct_chg_vals[-10:] < 0
                up_vol = amount[-10:][up_mask].mean() if up_mask.any() else 0
                down_vol = amount[-10:][down_mask].mean() if down_mask.any() else 1e-9
                if up_vol > down_vol * 1.2:
                    sustain_score += 0.15
                    r['volume_signal'] = '涨放量跌缩量'
                elif up_vol > down_vol:
                    sustain_score += 0.08
                    r['volume_signal'] = '量能正常'
                else:
                    sustain_score -= 0.05
                    r['volume_signal'] = '缩量上涨'
                    exit_signals.append('缩量上涨')
                if n >= 2:
                    vol_shrink = amount[-1] / (amount[-2] + 1e-9)
                    if vol_shrink < 0.5 and pct_chg_vals[-1] < 0:
                        sustain_score -= 0.10
                        exit_signals.append('缩量下跌')

            # 维度5: 均线支撑
            if n >= 20:
                ma5 = close[-6:-1].mean() if n >= 6 else close.mean()
                ma10 = close[-11:-1].mean() if n >= 11 else close.mean()
                ma20 = close[-21:-1].mean() if n >= 21 else close.mean()
                last_close = float(close[-1])
                ma_support = sum(1 for m in [ma5, ma10, ma20] if last_close > m)
                if ma_support == 3:
                    sustain_score += 0.10
                    r['ma_signal'] = '均线多头'
                elif ma_support >= 2:
                    sustain_score += 0.05
                    r['ma_signal'] = '部分支撑'
                else:
                    sustain_score -= 0.05
                    r['ma_signal'] = '破位'
                    exit_signals.append('均线破位')

            sustain_score = max(0, min(1, sustain_score))
            r['sustain_score'] = round(sustain_score, 3)
            r['exit_signal'] = '; '.join(exit_signals) if exit_signals else ''
            r['should_exit'] = sustain_score < cfg.layer6_sustain_score_min and bool(exit_signals)
            results.append(r)

        if not results:
            return pd.DataFrame()
        return pd.DataFrame(results)
    except Exception as e:
        logger.error(f"Layer6 持续性评估失败: {e}")
        return pd.DataFrame()
    finally:
        pass  # session connection managed by get_db()


# ============================================================
# 融合评分归一化（动态权重 + 持续性调节）
# ============================================================

def normalize_scores(factor_df: pd.DataFrame, launch_df: pd.DataFrame,
                     llm_data: Dict[str, Dict], overnight_df: pd.DataFrame,
                     sustain_df: pd.DataFrame = None,
                     weights: Dict[str, float] = None) -> pd.DataFrame:
    """统一评分归一化 v2.0（动态权重+持续性调节+LLM否决）"""
    if weights is None:
        weights = {'factor': 0.25, 'launch': 0.15, 'llm': 0.20, 'overnight': 0.40}

    merged = factor_df[['ts_code', 'total_score']].copy()
    merged = merged.rename(columns={'total_score': 'factor_raw'})

    if not launch_df.empty and 'launch_score' in launch_df.columns:
        merged['launch_raw'] = merged['ts_code'].map(
            dict(zip(launch_df['ts_code'], launch_df['launch_score']))
        ).fillna(0)
    else:
        merged['launch_raw'] = 0.5

    merged['llm_raw'] = merged['ts_code'].map(
        lambda c: llm_data.get(c, {}).get('llm_bonus', 0))
    merged['llm_veto'] = merged['ts_code'].map(
        lambda c: llm_data.get(c, {}).get('llm_veto', False))

    if not overnight_df.empty and 'overnight_score' in overnight_df.columns:
        merged['overnight_raw'] = merged['ts_code'].map(
            dict(zip(overnight_df['ts_code'], overnight_df['overnight_score']))
        ).fillna(0)
    else:
        merged['overnight_raw'] = 50

    if sustain_df is not None and not sustain_df.empty and 'sustain_score' in sustain_df.columns:
        merged['sustain_raw'] = merged['ts_code'].map(
            dict(zip(sustain_df['ts_code'], sustain_df['sustain_score']))
        ).fillna(0.5)
    else:
        merged['sustain_raw'] = 0.5

    # 归一化到0-100
    merged['factor_score'] = (merged['factor_raw'] * 100).clip(0, 100)
    merged['launch_score'] = (merged['launch_raw'] * 100).clip(0, 100)
    merged['llm_score'] = (merged['llm_raw'] / 30 * 100).clip(0, 100)
    merged['overnight_score'] = (merged['overnight_raw'] / 120 * 100).clip(0, 100)
    merged['sustain_score'] = (merged['sustain_raw'] * 100).clip(0, 100)

    # 加权融合
    merged['meta_score'] = round(
        merged['factor_score'] * weights.get('factor', 0.25) +
        merged['launch_score'] * weights.get('launch', 0.15) +
        merged['llm_score'] * weights.get('llm', 0.20) +
        merged['overnight_score'] * weights.get('overnight', 0.40), 2)

    # 持续性调节
    sustain_penalty = merged['sustain_raw'].apply(
        lambda s: -10 * (0.3 - s) if s < 0.3 else 0)
    merged['meta_score'] = (merged['meta_score'] + sustain_penalty).round(2)

    # LLM否决
    merged.loc[merged['llm_veto'], 'meta_score'] = 0
    merged.loc[merged['llm_veto'], 'veto_reason'] = merged[merged['llm_veto']]['ts_code'].map(
        lambda c: llm_data.get(c, {}).get('veto_reason', ''))

    # 附加信息
    if not overnight_df.empty:
        for c in ['pct_chg', 'turnover', 'vol_ratio', 'tags', 'pool']:
            if c in overnight_df.columns:
                merged[c] = merged['ts_code'].map(
                    dict(zip(overnight_df['ts_code'], overnight_df[c])))
    if not factor_df.empty:
        for c in ['momentum', 'rsi', 'macd_signal', 'ema_signal', 'adx_signal', 'close']:
            if c in factor_df.columns:
                merged[c] = merged['ts_code'].map(
                    dict(zip(factor_df['ts_code'], factor_df[c])))
    merged['llm_sources'] = merged['ts_code'].map(
        lambda c: llm_data.get(c, {}).get('sources', []))

    return merged.sort_values('meta_score', ascending=False).reset_index(drop=True)


# ============================================================
# 主编排器
# ============================================================

class MetaStrategyEngine:
    """融合元策略引擎 v2.0"""

    def __init__(self, cfg: MetaStrategyConfig = None):
        self.cfg = cfg or DEFAULT_META_CONFIG
        self._stats = {}

    def run(self, trade_date: date = None, verbose: bool = True) -> pd.DataFrame:
        """运行完整七层漏斗 v2.0"""
        t_start = time.time()
        if trade_date is None:
            trade_date = datetime.now(BEIJING_TZ).date()

        if verbose:
            print("=" * 70)
            print("  融合元策略引擎 v2.0 (七层漏斗)")
            print(f"  日期: {trade_date}")
            print("=" * 70)

        self._stats = {'trade_date': str(trade_date)}

        # ── Layer 0: 大盘风控 + 市场状态 ──
        if verbose:
            print(f"\n{'─'*60}")
            print("  [Layer 0] 大盘风控 + 市场状态识别")
            print(f"{'─'*60}")
        market_risk = check_market_risk(trade_date, self.cfg)
        self._stats['layer0'] = market_risk
        weights = market_risk.get('weights', self.cfg.weights_oscillate)

        if verbose:
            status = "PASS" if market_risk['passed'] else "WARN"
            regime = market_risk.get('regime', 'oscillate')
            regime_score = market_risk.get('regime_score', 0)
            print(f"  上涨{market_risk['advancers']}家 下跌{market_risk['decliners']}家  "
                  f"广度{market_risk['breadth_ratio']:.2%}  {status}")
            print(f"  市场状态: {regime} (得分{regime_score:.2f})  "
                  f"仓位上限: {market_risk['position_cap']:.0%}")
            print(f"  动态权重: factor={weights.get('factor',0):.0%} "
                  f"launch={weights.get('launch',0):.0%} "
                  f"llm={weights.get('llm',0):.0%} "
                  f"overnight={weights.get('overnight',0):.0%}")
            if not market_risk['passed']:
                print(f"  原因: {market_risk['reason']}")

        # ── Layer 1: 多因子扫描 ──
        if verbose:
            print(f"\n{'─'*60}")
            print("  [Layer 1] 多因子全市场扫描")
            print(f"{'─'*60}")
        t1 = time.time()
        factor_df = run_multi_factor_scan(trade_date, self.cfg)
        t1_elapsed = time.time() - t1
        self._stats['layer1_count'] = len(factor_df)
        if verbose:
            print(f"  扫描结果: {len(factor_df)} 只  ({t1_elapsed:.1f}s)")
        if factor_df.empty:
            if verbose:
                print("  Layer 1 无候选，退出")
            return pd.DataFrame()

        l1_codes = factor_df['ts_code'].tolist()

        # ── Layer 2: 基本面+流动性 ──
        if verbose:
            print(f"\n{'─'*60}")
            print("  [Layer 2] 基本面+流动性过滤")
            print(f"{'─'*60}")
        t2 = time.time()
        l2_codes = run_fundamental_liquidity_filter(l1_codes, trade_date, self.cfg)
        t2_elapsed = time.time() - t2
        self._stats['layer2_count'] = len(l2_codes)
        if verbose:
            print(f"  {len(l1_codes)} -> {len(l2_codes)} 只  ({t2_elapsed:.1f}s)")
        if not l2_codes:
            if verbose:
                print("  Layer 2 全部淘汰，退出")
            return pd.DataFrame()

        # ── Layer 3: 启动信号 ──
        if verbose:
            print(f"\n{'─'*60}")
            print("  [Layer 3] 启动信号识别")
            print(f"{'─'*60}")
        t3 = time.time()
        launch_df = detect_launch_signals(l2_codes, trade_date, self.cfg)
        t3_elapsed = time.time() - t3
        self._stats['layer3_count'] = len(launch_df)
        if verbose:
            print(f"  启动信号: {len(launch_df)} 只  ({t3_elapsed:.1f}s)")
        l3_codes = launch_df['ts_code'].tolist() if not launch_df.empty else l2_codes

        # ── Layer 4: LLM加成 ──
        if verbose:
            print(f"\n{'─'*60}")
            print("  [Layer 4] LLM事件驱动加成")
            print(f"{'─'*60}")
        t4 = time.time()
        llm_data = get_llm_boost(l3_codes, trade_date, self.cfg)
        t4_elapsed = time.time() - t4
        llm_covered = sum(1 for v in llm_data.values() if v.get('llm_bonus', 0) > 0)
        llm_vetoed = sum(1 for v in llm_data.values() if v.get('llm_veto', False))
        self._stats['layer4_covered'] = llm_covered
        self._stats['layer4_vetoed'] = llm_vetoed
        if verbose:
            print(f"  LLM覆盖: {llm_covered}/{len(l3_codes)} 只  "
                  f"否决: {llm_vetoed} 只  ({t4_elapsed:.1f}s)")

        # ── Layer 5: 八步法精细评分 ──
        if verbose:
            print(f"\n{'─'*60}")
            print("  [Layer 5] 八步法精细评分")
            print(f"{'─'*60}")
        t5 = time.time()
        overnight_df = compute_overnight_score(l3_codes, trade_date, self.cfg)
        t5_elapsed = time.time() - t5
        self._stats['layer5_count'] = len(overnight_df)
        if verbose:
            if not overnight_df.empty and 'pool' in overnight_df.columns:
                pool_dist = overnight_df['pool'].value_counts().to_dict()
                print(f"  评分完成: {len(overnight_df)} 只  "
                      f"双池分布: {pool_dist}  ({t5_elapsed:.1f}s)")
            else:
                print(f"  评分完成: {len(overnight_df)} 只  ({t5_elapsed:.1f}s)")

        # ── Layer 6: 持续性评估 ──
        if verbose:
            print(f"\n{'─'*60}")
            print("  [Layer 6] 持续性评估")
            print(f"{'─'*60}")
        t6 = time.time()
        sustain_df = evaluate_sustainability(l3_codes, trade_date, self.cfg)
        t6_elapsed = time.time() - t6
        self._stats['layer6_count'] = len(sustain_df)
        if verbose:
            if not sustain_df.empty:
                exit_count = sustain_df['should_exit'].sum() if 'should_exit' in sustain_df.columns else 0
                print(f"  持续性评估: {len(sustain_df)} 只  "
                      f"建议退出: {exit_count} 只  ({t6_elapsed:.1f}s)")
            else:
                print(f"  持续性评估: 无数据  ({t6_elapsed:.1f}s)")

        # ── 融合评分 ──
        if verbose:
            print(f"\n{'─'*60}")
            print("  [融合] 评分归一化 + 排序")
            print(f"{'─'*60}")
        result_df = normalize_scores(
            factor_df, launch_df, llm_data, overnight_df,
            sustain_df=sustain_df, weights=weights)

        # LLM否决过滤
        vetoed_codes = set()
        for ts_code, data in llm_data.items():
            if data.get('llm_veto', False):
                vetoed_codes.add(ts_code)
        if vetoed_codes:
            result_df = result_df[~result_df['ts_code'].isin(vetoed_codes)]

        # 八步法一票否决：overnight_score < min_quant_score 的候选降权
        if not overnight_df.empty and 'overnight_score' in overnight_df.columns:
            low_overnight = set(overnight_df[
                overnight_df['overnight_score'] < self.cfg.layer5_min_quant_score
            ]['ts_code'].tolist())
            result_df.loc[result_df['ts_code'].isin(low_overnight), 'meta_score'] *= 0.5

        # 限制最终候选数
        if len(result_df) > self.cfg.max_final_candidates:
            result_df = result_df.head(self.cfg.max_final_candidates)

        total_elapsed = time.time() - t_start
        self._stats['total_elapsed'] = round(total_elapsed, 1)
        self._stats['final_count'] = len(result_df)

        if verbose:
            print(f"\n{'='*70}")
            print(f"  最终候选: {len(result_df)} 只  总耗时: {total_elapsed:.1f}s")
            print(f"{'='*70}")
            if not result_df.empty:
                print(f"\n  {'排名':<4} {'代码':<12} {'融合分':>6} "
                      f"{'池':<8} {'涨幅%':>6} {'换手%':>6} {'量比':>5} "
                      f"{'标签'}")
                print(f"  {'─'*70}")
                for i, row in result_df.iterrows():
                    rank = result_df.index.get_loc(i) + 1
                    pool = row.get('pool', '-')
                    pct = row.get('pct_chg', 0)
                    turn = row.get('turnover', 0)
                    vr = row.get('vol_ratio', 0)
                    tags = row.get('tags', [])
                    tags_str = ','.join(tags[:3]) if isinstance(tags, list) else str(tags)[:30]
                    print(f"  {rank:<4} {row['ts_code']:<12} {row['meta_score']:>6.1f} "
                          f"{pool:<8} {pct:>6.2f} {turn:>6.2f} {vr:>5.1f} "
                          f"{tags_str}")

        # 保存结果
        self._save_result(result_df, trade_date)

        return result_df

    def _save_result(self, result_df: pd.DataFrame, trade_date: date):
        """保存结果到文件"""
        try:
            output_dir = Path(self.cfg.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # CSV
            csv_path = output_dir / f"meta_strategy_{trade_date}.csv"
            result_df.to_csv(csv_path, index=False, encoding='utf-8-sig')

            # JSON (含统计)
            json_path = output_dir / f"meta_strategy_{trade_date}.json"
            output = {
                'trade_date': str(trade_date),
                'version': '2.0',
                'stats': self._stats,
                'candidates': result_df.to_dict(orient='records') if not result_df.empty else [],
            }
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(output, f, ensure_ascii=False, indent=2, default=str)

            logger.info(f"结果已保存: {csv_path}, {json_path}")
        except Exception as e:
            logger.warning(f"保存结果失败: {e}")

    def get_stats(self) -> Dict:
        """获取运行统计"""
        return self._stats


# ============================================================
# CLI 入口
# ============================================================

def main():
    """命令行入口"""
    import argparse
    parser = argparse.ArgumentParser(description='融合元策略引擎 v2.0')
    parser.add_argument('--date', type=str, default=None, help='交易日期 (YYYY-MM-DD)')
    parser.add_argument('--top', type=int, default=10, help='最终候选数量')
    parser.add_argument('--output', type=str, default='./results', help='输出目录')
    parser.add_argument('--quiet', action='store_true', help='静默模式')
    args = parser.parse_args()

    cfg = DEFAULT_META_CONFIG
    cfg.max_final_candidates = args.top
    cfg.output_dir = args.output
    cfg.verbose = not args.quiet

    trade_date = None
    if args.date:
        trade_date = date.fromisoformat(args.date)

    engine = MetaStrategyEngine(cfg)
    result = engine.run(trade_date=trade_date, verbose=cfg.verbose)

    if result.empty:
        print("\n无候选股票")
    else:
        print(f"\n共 {len(result)} 只候选")


if __name__ == '__main__':
    main()
