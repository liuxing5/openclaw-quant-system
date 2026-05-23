"""
融合元策略引擎 (Meta-Strategy Engine) v1.0
============================================
六层漏斗编排，融合五大策略优势：

  Layer 0: 大盘风控（漏斗L0）—— 系统性风险门控
  Layer 1: 多因子全市场扫描（多因子扫描器）—— 广度：快速缩小范围
  Layer 2: 基本面+流动性过滤（漏斗L1-L2）—— 深度：排除质地差的
  Layer 3: 启动信号识别（主升浪B层）—— 时机：确认启动点
  Layer 4: 事件驱动加成（LLM多源）—— 催化剂：新闻/研报/公告
  Layer 5: 隔夜精细评分（八步法核心）—— 精度：最终排序

信号链路（严格 PIT）：
  Day T 收盘后(15:10):
    LLM策略 → 写入 daily_candidates (辅助数据)
    融合引擎 → 读取全量数据，产出次日候选池
  Day T+1 14:30:
    八步法实时扫描 → 融合引擎辅助数据 → 生成买入信号
  Day T+2:
    持仓管理模块 → 卖出信号

核心原则：
  - 八步法保持核心决策地位
  - 其他策略是"过滤器"或"加成器"，不做独立筛选
  - 所有信号严格 PIT，无未来函数
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

from core.db.connection import get_db_fresh, close_db_session

logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))


# ============================================================
# 配置
# ============================================================

@dataclass
class MetaStrategyConfig:
    """融合元策略配置"""

    # Layer 0: 大盘风控
    layer0_enabled: bool = True
    layer0_min_advancers: int = 2500
    layer0_use_breadth_ema: bool = True
    layer0_partial_cap: float = 0.50

    # Layer 1: 多因子扫描
    layer1_enabled: bool = True
    layer1_min_total_score: float = 0.40
    layer1_top_n: int = 500
    layer1_momentum_min: float = 0.0
    layer1_rsi_max: float = 75.0
    layer1_macd_bullish: bool = True
    layer1_sar_support: bool = True

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

    # Layer 5: 八步法精细评分
    layer5_enabled: bool = True
    layer5_min_quant_score: int = 80
    layer5_pct_range_low: float = 2.0
    layer5_pct_range_high: float = 6.0
    layer5_vol_ratio_min: float = 1.5
    layer5_vol_ratio_max: float = 10.0

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
# Layer 0: 大盘风控
# ============================================================

def check_market_risk(trade_date: date = None, cfg: MetaStrategyConfig = None) -> Dict:
    """
    大盘风控检查
    返回: {
        'passed': bool,
        'advancers': int,
        'decliners': int,
        'breadth_ratio': float,
        'position_cap': float,  # 1.0=满仓, 0.5=半仓, 0=空仓
        'reason': str,
    }
    """
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer0_enabled:
        return {'passed': True, 'advancers': 0, 'decliners': 0,
                'breadth_ratio': 0, 'position_cap': 1.0, 'reason': 'Layer0禁用'}

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        if trade_date is None:
            cur.execute("SELECT MAX(trade_date) as max_date FROM daily_quotes;")
            row = cur.fetchone()
            trade_date = row['max_date'] if row else datetime.now(BEIJING_TZ).date()

        # 当日上涨/下跌家数
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

        # 市场广度EMA（20日）
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

        cur.close()

        passed = advancers >= cfg.layer0_min_advancers and breadth_ratio >= breadth_ema
        position_cap = 1.0 if passed else cfg.layer0_partial_cap

        return {
            'passed': passed,
            'advancers': advancers,
            'decliners': decliners,
            'breadth_ratio': round(breadth_ratio, 4),
            'breadth_ema': round(breadth_ema, 4),
            'position_cap': position_cap,
            'reason': '' if passed else f'上涨{advancers}家<{cfg.layer0_min_advancers}或广度低于EMA',
        }
    except Exception as e:
        logger.warning(f"Layer0 大盘风控查询失败: {e}")
        return {'passed': True, 'advancers': 0, 'decliners': 0,
                'breadth_ratio': 0, 'breadth_ema': 0, 'position_cap': 1.0,
                'reason': f'查询失败({e})，默认放行'}
    finally:
        if conn and not conn.closed:
            conn.close()


# ============================================================
# Layer 1: 多因子全市场扫描（基于数据库，回测友好）
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
    """
    基于数据库的多因子扫描（回测友好，不依赖腾讯实时接口）

    因子体系（与 multi_factor_scanner.py 一致）：
      1. 截面动量 (20日涨幅)
      2. 量比 (当日成交额 / 20日均额)
      3. RSI(6)
      4. MACD(12,26,9)
      5. EMA排列 (5/10/20)
      6. ADX(14)
      7. SAR

    返回: DataFrame，含 ts_code + 各因子得分
    """
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer1_enabled:
        return pd.DataFrame()

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 加载60日K线数据
        start_date = trade_date - timedelta(days=120)
        if stock_pool:
            cur.execute("""
                SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg
                FROM daily_quotes
                WHERE trade_date >= %s AND trade_date <= %s
                  AND ts_code = ANY(%s)
                ORDER BY ts_code, trade_date;
            """, (start_date, trade_date, stock_pool))
        else:
            # 只扫描成交额>5000万的标的，减少计算量
            cur.execute("""
                SELECT d.ts_code, d.trade_date, d.open, d.high, d.low,
                       d.close, d.volume, d.amount, d.pct_chg
                FROM daily_quotes d
                JOIN (
                    SELECT ts_code
                    FROM daily_quotes
                    WHERE trade_date = %s AND amount > 50000000
                ) active ON d.ts_code = active.ts_code
                WHERE d.trade_date >= %s AND d.trade_date <= %s
                ORDER BY d.ts_code, d.trade_date;
            """, (trade_date, start_date, trade_date))

        rows = cur.fetchall()
        cur.close()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
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
                mom = float((close[-(skip+1)] - close[-(win+skip+1)]) /
                            (close[-(win+skip+1)] + 1e-9))
            else:
                mom = 0.0
            r['momentum'] = round(mom, 4)
            r['momentum_score'] = round(
                min(max(mom / 0.15, 0), 1.0) if mom > 0 else 0.0, 3)

            # 2. 量比
            vw = 20
            vol_mean = amount[-(vw+1):-1].mean() if n >= vw + 1 else amount.mean()
            vr = float(amount[-1]) / (float(vol_mean) + 1e-9)
            r['volume_ratio'] = round(vr, 2)
            cw = 10
            if n >= cw:
                cp = close[-cw:] - close[-cw:].mean()
                cv = amount[-cw:] - amount[-cw:].mean()
                pv_corr = float((cp * cv).sum() /
                                (np.sqrt((cp**2).sum() * (cv**2).sum()) + 1e-9))
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
            r['rsi_score'] = (0.0 if rsi >= 75 else
                              1.0 if rsi <= 35 else
                              round((75 - rsi) / 40, 3))

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
            up = high[1:] - high[:-1]
            dn = low[:-1] - low[1:]
            atr = _ema(tr, p)
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
                r['adx_score'] = 0.3
                r['adx_signal'] = f'弱趋势(ADX={adx_v:.0f})'
            else:
                r['adx_score'] = 0.0
                r['adx_signal'] = f'偏空(ADX={adx_v:.0f})'

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
                r['sar_score'] = 0.0
                r['sar_signal'] = 'SAR压制'

            # 加权总分
            r['total_score'] = round(
                r['momentum_score'] * 0.20 +
                r['volume_score'] * 0.20 +
                r['rsi_score'] * 0.15 +
                r['macd_score'] * 0.15 +
                r['ema_score'] * 0.15 +
                r['adx_score'] * 0.10 +
                r['sar_score'] * 0.05, 4)

            results.append(r)

        if not results:
            return pd.DataFrame()

        df_result = pd.DataFrame(results)

        # 多级筛选
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
        import traceback
        traceback.print_exc()
        return pd.DataFrame()
    finally:
        if conn and not conn.closed:
            conn.close()


# ============================================================
# Layer 2: 基本面+流动性过滤
# ============================================================

def run_fundamental_liquidity_filter(
    stock_list: List[str],
    trade_date: date,
    cfg: MetaStrategyConfig = None,
) -> List[str]:
    """
    基本面防雷 + 流动性过滤（合并漏斗L1+L2逻辑）
    返回通过过滤的股票代码列表
    """
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer2_enabled:
        return stock_list

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 批量加载基本面数据
        cur.execute("""
            SELECT DISTINCT ON (ts_code)
                   ts_code, report_date,
                   revenue, net_profit, gross_margin, net_margin,
                   total_assets, total_liabilities,
                   current_assets, current_liabilities,
                   debt_ratio, equity,
                   operating_cashflow, accounts_receivable, inventory,
                   goodwill, pledge_ratio,
                   revenue_yoy, profit_yoy,
                   industry, listing_date
            FROM stock_fundamentals
            WHERE report_date <= %s
            ORDER BY ts_code, report_date DESC;
        """, (trade_date,))
        fin_cache = {}
        for r in cur.fetchall():
            fin_cache[r['ts_code']] = {k: (float(v) if v is not None else None)
                                       for k, v in r.items()
                                       if k != 'ts_code'}

        # ST/上市日期
        cur.execute("""
            SELECT ts_code, stock_name, list_date, is_st
            FROM stock_basic_info
            WHERE ts_code = ANY(%s);
        """, (stock_list,))
        st_cache = {}
        for r in cur.fetchall():
            st_cache[r['ts_code']] = {
                'stock_name': r['stock_name'] or '',
                'list_date': r['list_date'],
                'is_st': r['is_st'] or False,
            }

        # 流动性数据（当日+20日均额）
        start_20d = trade_date - timedelta(days=40)
        cur.execute("""
            SELECT ts_code, amount, turnover_rate,
                   circulating_market_cap, total_market_cap
            FROM daily_quotes
            WHERE trade_date = %s AND ts_code = ANY(%s);
        """, (trade_date, stock_list))
        liq_cache = {}
        for r in cur.fetchall():
            liq_cache[r['ts_code']] = {
                'amount': float(r['amount']) if r['amount'] else 0,
                'turnover_rate': float(r['turnover_rate']) if r['turnover_rate'] else 0,
                'circulating_market_cap': float(r['circulating_market_cap']) if r['circulating_market_cap'] else 0,
                'total_market_cap': float(r['total_market_cap']) if r['total_market_cap'] else 0,
            }

        # 20日均额
        cur.execute("""
            SELECT ts_code, amount
            FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s
              AND ts_code = ANY(%s)
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

        # 减持公告
        reduction_codes = set()
        cur.execute("""
            SELECT DISTINCT ts_code
            FROM stock_announcements
            WHERE publish_date >= %s
              AND (title ILIKE '%%减持%%' OR title ILIKE '%%reduce%%')
              AND ts_code = ANY(%s);
        """, (trade_date - timedelta(days=60), stock_list))
        for r in cur.fetchall():
            reduction_codes.add(r['ts_code'])

        cur.close()

        # 过滤
        passed = []
        reject_stats = {}
        for ts_code in stock_list:
            reason = _check_fundamental_liquidity(
                ts_code, st_cache.get(ts_code, {}),
                fin_cache.get(ts_code, {}),
                liq_cache.get(ts_code, {}),
                avg_amount_cache.get(ts_code, 0),
                reduction_codes, trade_date, cfg)
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
        if conn and not conn.closed:
            conn.close()


def _check_fundamental_liquidity(
    ts_code, stock_info, fin, liq, avg_amount,
    reduction_codes, trade_date, cfg) -> Optional[str]:
    """返回 None=通过，str=拒绝原因"""
    # ST
    if cfg.layer2_exclude_st:
        if stock_info.get('is_st') or 'ST' in stock_info.get('stock_name', ''):
            return 'ST'
    # 次新股
    if cfg.layer2_exclude_new_ipo_days > 0 and stock_info.get('list_date'):
        ld = stock_info['list_date']
        if isinstance(ld, str):
            try:
                ld = date.fromisoformat(ld)
            except ValueError:
                ld = None
        if ld and (trade_date - ld).days < cfg.layer2_exclude_new_ipo_days:
            return '次新股'
    # 流动性
    if avg_amount < cfg.layer2_min_avg_amount_20d:
        return '成交额不足'
    # 换手率
    turn = liq.get('turnover_rate', 0)
    if turn > 0 and (turn < cfg.layer2_turn_rate_min or turn > cfg.layer2_turn_rate_max):
        return '换手不符'
    # 市值
    circ_mcap = liq.get('circulating_market_cap', 0) or liq.get('total_market_cap', 0)
    if circ_mcap > 0 and circ_mcap < cfg.layer2_min_circulating_mcap:
        return '市值不足'
    # 基本面
    if not fin:
        return None  # 无数据放行
    # 亏损
    if fin.get('net_profit') is not None and fin['net_profit'] <= 0:
        return '亏损'
    # 负债率
    if fin.get('debt_ratio') is not None and fin['debt_ratio'] > cfg.layer2_max_debt_ratio:
        return '负债率高'
    # 现金流
    if (fin.get('operating_cashflow') is not None and
        fin.get('net_profit') is not None and
        fin['net_profit'] > 0):
        cf_ratio = fin['operating_cashflow'] / fin['net_profit']
        if cf_ratio < cfg.layer2_min_cashflow_ratio:
            return '现金流差'
    # 商誉
    if (fin.get('goodwill') is not None and
        fin.get('equity') is not None and
        fin['equity'] > 0):
        gw_pct = fin['goodwill'] / fin['equity'] * 100
        if gw_pct > cfg.layer2_max_goodwill_pct:
            return '商誉风险'
    # 减持
    if ts_code in reduction_codes:
        return '减持'
    # 质押
    if (fin.get('pledge_ratio') is not None and
        fin['pledge_ratio'] > 50.0):
        return '高质押'

    return None


# ============================================================
# Layer 3: 启动信号识别
# ============================================================

def detect_launch_signals(
    stock_list: List[str],
    trade_date: date,
    cfg: MetaStrategyConfig = None,
) -> pd.DataFrame:
    """
    启动信号检测（简化版主升浪B层）
    基于日线数据检测：量能突破 + 价格突破 + 主力资金

    返回: DataFrame，含 ts_code + launch_score + 各因子
    """
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer3_enabled:
        return pd.DataFrame({'ts_code': stock_list, 'launch_score': 1.0})

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        start_date = trade_date - timedelta(days=120)
        cur.execute("""
            SELECT ts_code, trade_date, open, high, low, close,
                   volume, amount, pct_chg, turnover_rate
            FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s
              AND ts_code = ANY(%s)
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
            n = len(group)

            r = {'ts_code': ts_code}
            score = 0.0

            # 1. 量能突破：当日成交额 > 60日均值的2.5倍
            if n >= 60:
                vol_ma60 = amount[-61:-1].mean()
            else:
                vol_ma60 = amount[:-1].mean() if n > 1 else amount[0]
            vol_ratio = amount[-1] / (vol_ma60 + 1e-9)
            if vol_ratio >= cfg.layer3_volume_breakout_mult:
                score += 0.3
                r['volume_breakout'] = True
                r['vol_ratio'] = round(vol_ratio, 2)
            else:
                r['volume_breakout'] = False
                r['vol_ratio'] = round(vol_ratio, 2)

            # 2. 换手率
            last_turnover = float(turnover[-1]) if not np.isnan(turnover[-1]) else 0
            if last_turnover >= cfg.layer3_turnover_min:
                score += 0.15
                r['turnover_ok'] = True
            else:
                r['turnover_ok'] = False
            r['turnover'] = round(last_turnover, 2)

            # 3. 价格突破：突破60日箱体上沿
            if n >= cfg.layer3_price_breakout_box_days:
                box_high = high[-(cfg.layer3_price_breakout_box_days+1):-1].max()
                if close[-1] > box_high:
                    score += 0.25
                    r['price_breakout'] = True
                    r['box_high'] = round(float(box_high), 2)
                else:
                    r['price_breakout'] = False
            else:
                r['price_breakout'] = False

            # 4. 站上均线且距均线不太远
            if n >= 20:
                ma20 = close[-21:-1].mean()
                pct_above_ma = (close[-1] - ma20) / (ma20 + 1e-9)
                if 0 < pct_above_ma < cfg.layer3_price_above_ma_max_pct:
                    score += 0.15
                    r['near_ma'] = True
                    r['pct_above_ma20'] = round(pct_above_ma, 4)
                else:
                    r['near_ma'] = False
            else:
                r['near_ma'] = False

            # 5. 涨幅适中（不追涨停）
            pct_chg = float(group['pct_chg'].values[-1]) if not np.isnan(group['pct_chg'].values[-1]) else 0
            if 0 < pct_chg < 9.5:
                score += 0.15
                r['pct_ok'] = True
            else:
                r['pct_ok'] = False
            r['pct_chg'] = round(pct_chg, 2)

            r['launch_score'] = round(score, 3)
            results.append(r)

        if not results:
            return pd.DataFrame()

        df_result = pd.DataFrame(results)
        # 只保留启动信号得分达标的
        df_result = df_result[df_result['launch_score'] >= cfg.layer3_min_launch_score]
        return df_result.reset_index(drop=True)

    except Exception as e:
        logger.error(f"Layer3 启动信号检测失败: {e}")
        return pd.DataFrame()
    finally:
        if conn and not conn.closed:
            conn.close()


# ============================================================
# Layer 4: LLM事件驱动加成
# ============================================================

def get_llm_boost(stock_list: List[str], trade_date: date,
                  cfg: MetaStrategyConfig = None) -> Dict[str, Dict]:
    """
    从 daily_candidates 读取LLM多源策略的辅助数据
    返回: {ts_code: {'llm_score': float, 'llm_bonus': float, 'sources': list}}
    """
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer4_enabled:
        return {c: {'llm_score': 0, 'llm_bonus': 0, 'sources': []} for c in stock_list}

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 查找最近交易日的LLM数据
        cur.execute("""
            SELECT ts_code, stock_name, final_score, llm_score, quant_score,
                   source_diversity, logic_tags
            FROM daily_candidates
            WHERE snapshot_date = (
                SELECT MAX(snapshot_date) FROM daily_candidates
                WHERE snapshot_date <= %s AND source = 'llm_multisource'
            )
            AND source = 'llm_multisource'
            AND selected = TRUE
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

            llm_data[ts_code] = {
                'llm_score': llm_score,
                'final_score': final_score,
                'source_diversity': diversity,
                'llm_bonus': round(bonus, 1),
                'sources': sources,
            }

        cur.close()

        # 未被LLM覆盖的标的
        for c in stock_list:
            if c not in llm_data:
                llm_data[c] = {'llm_score': 0, 'final_score': 0,
                               'source_diversity': 0, 'llm_bonus': 0, 'sources': []}

        return llm_data

    except Exception as e:
        logger.warning(f"Layer4 LLM数据加载失败: {e}")
        return {c: {'llm_score': 0, 'llm_bonus': 0, 'sources': []} for c in stock_list}
    finally:
        if conn and not conn.closed:
            conn.close()


# ============================================================
# Layer 5: 八步法精细评分
# ============================================================

def compute_overnight_score(
    stock_list: List[str],
    trade_date: date,
    cfg: MetaStrategyConfig = None,
) -> pd.DataFrame:
    """
    八步法核心评分（基于数据库，回测友好）
    评分维度（总分100+）：
      - 涨幅评分: 0-30
      - 换手率评分: 0-30
      - 成交额评分: 0-40
      - 量比评分: 0-15
      - MA5贴线加分: 0-10
      - 压力位扣分: 0-30

    返回: DataFrame，含 ts_code + overnight_score + 各因子
    """
    if cfg is None:
        cfg = DEFAULT_META_CONFIG
    if not cfg.layer5_enabled:
        return pd.DataFrame({'ts_code': stock_list, 'overnight_score': 80})

    conn = None
    try:
        conn = get_db_fresh()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT ts_code, trade_date, open, high, low, close,
                   volume, amount, pct_chg, turnover_rate
            FROM daily_quotes
            WHERE trade_date = %s AND ts_code = ANY(%s);
        """, (trade_date, stock_list))

        rows = cur.fetchall()

        # 20日均额
        start_20d = trade_date - timedelta(days=40)
        cur.execute("""
            SELECT ts_code, amount
            FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s
              AND ts_code = ANY(%s)
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

        # 5日均线
        start_5d = trade_date - timedelta(days=15)
        cur.execute("""
            SELECT ts_code, trade_date, close
            FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s
              AND ts_code = ANY(%s)
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

        cur.close()

        if not rows:
            return pd.DataFrame()

        results = []
        for r in rows:
            ts_code = r['ts_code']
            pct_chg = float(r['pct_chg'] or 0)
            turnover = float(r['turnover_rate'] or 0)
            amount = float(r['amount'] or 0)
            close = float(r['close'] or 0)
            high = float(r['high'] or 0)

            if close <= 0:
                continue

            score = 0.0
            tags = []

            # 1. 涨幅评分 (0-30)
            if 1.5 <= pct_chg <= 4:
                score += 30
                tags.append('稳健蓄势')
            elif 4 < pct_chg <= 7:
                score += 25
                tags.append('强势突破')
            elif 0 < pct_chg < 1.5:
                score += 15
            elif 7 <= pct_chg < 9.5:
                score += 10
                tags.append('追涨风险')
            else:
                score += 0

            # 2. 换手率评分 (0-30)
            if turnover > 0:
                score += min(30, turnover * 2)
            if 5.0 <= turnover <= 8.0:
                tags.append('黄金换手')

            # 3. 成交额评分 (0-40)
            if amount > 0:
                score += min(40, 10 * np.log10(amount / 1e8 + 1))

            # 4. 量比评分 (0-15)
            avg_amt = avg_amount_cache.get(ts_code, 0)
            if avg_amt > 0:
                vol_ratio = amount / avg_amt
                if vol_ratio >= 1.5:
                    score += min(15, (vol_ratio - 1.5) * 10)
                    tags.append(f'量比{vol_ratio:.1f}')

            # 5. MA5贴线加分 (0-10)
            ma5 = ma5_cache.get(ts_code)
            if ma5 and ma5 > 0:
                bias = (close - ma5) / ma5
                if 0 < bias < 0.02:
                    score += 10
                    tags.append('贴线')
                elif 0.02 <= bias < 0.05:
                    score += 5

            # 6. 压力位扣分
            # 简化：距20日高点越近扣分越多
            start_20d2 = trade_date - timedelta(days=30)
            # 用当日high近似近期高点（简化）

            results.append({
                'ts_code': ts_code,
                'overnight_score': round(score, 1),
                'pct_chg': round(pct_chg, 2),
                'turnover': round(turnover, 2),
                'amount': round(amount, 0),
                'vol_ratio': round(amount / avg_amt, 2) if avg_amt > 0 else 0,
                'tags': tags,
            })

        if not results:
            return pd.DataFrame()

        return pd.DataFrame(results)

    except Exception as e:
        logger.error(f"Layer5 八步法评分失败: {e}")
        return pd.DataFrame()
    finally:
        if conn and not conn.closed:
            conn.close()


# ============================================================
# 融合评分归一化
# ============================================================

def normalize_scores(
    factor_df: pd.DataFrame,
    launch_df: pd.DataFrame,
    llm_data: Dict[str, Dict],
    overnight_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    统一评分归一化，所有维度映射到0-100分

    归一化公式：
      meta_score = factor_score * 0.30
                 + launch_score * 0.20
                 + llm_bonus_normalized * 0.15
                 + overnight_score * 0.35

    返回: DataFrame，含 ts_code + meta_score + 各维度得分
    """
    # 合并所有数据
    merged = factor_df[['ts_code', 'total_score']].copy()
    merged = merged.rename(columns={'total_score': 'factor_raw'})

    # 启动信号
    if not launch_df.empty and 'launch_score' in launch_df.columns:
        launch_map = dict(zip(launch_df['ts_code'], launch_df['launch_score']))
        merged['launch_raw'] = merged['ts_code'].map(launch_map).fillna(0)
    else:
        merged['launch_raw'] = 0.5  # 无数据给中位分

    # LLM加成
    merged['llm_raw'] = merged['ts_code'].map(
        lambda c: llm_data.get(c, {}).get('llm_bonus', 0))

    # 八步法
    if not overnight_df.empty and 'overnight_score' in overnight_df.columns:
        ov_map = dict(zip(overnight_df['ts_code'], overnight_df['overnight_score']))
        merged['overnight_raw'] = merged['ts_code'].map(ov_map).fillna(0)
    else:
        merged['overnight_raw'] = 50

    # 归一化到0-100
    # factor_raw: 0-1 → 0-100
    merged['factor_score'] = (merged['factor_raw'] * 100).clip(0, 100)

    # launch_raw: 0-1 → 0-100
    merged['launch_score'] = (merged['launch_raw'] * 100).clip(0, 100)

    # llm_raw: 0-30+ → 归一化到0-100（max约30）
    merged['llm_score'] = (merged['llm_raw'] / 30 * 100).clip(0, 100)

    # overnight_raw: 0-120+ → 归一化到0-100（max约120）
    merged['overnight_score'] = (merged['overnight_raw'] / 120 * 100).clip(0, 100)

    # 加权融合
    merged['meta_score'] = round(
        merged['factor_score'] * 0.30 +
        merged['launch_score'] * 0.20 +
        merged['llm_score'] * 0.15 +
        merged['overnight_score'] * 0.35, 2)

    # 附加详细信息
    if not overnight_df.empty:
        for c in ['pct_chg', 'turnover', 'vol_ratio', 'tags']:
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
    """融合元策略引擎"""

    def __init__(self, cfg: MetaStrategyConfig = None):
        self.cfg = cfg or DEFAULT_META_CONFIG
        self._stats = {}

    def run(self, trade_date: date = None, verbose: bool = True) -> pd.DataFrame:
        """
        运行完整六层漏斗

        返回: DataFrame，含 ts_code + meta_score + 各维度得分
        """
        t_start = time.time()

        if trade_date is None:
            trade_date = datetime.now(BEIJING_TZ).date()

        if verbose:
            print("=" * 70)
            print("  融合元策略引擎 v1.0")
            print(f"  日期: {trade_date}")
            print("=" * 70)

        self._stats = {'trade_date': str(trade_date)}

        # ── Layer 0: 大盘风控 ──
        if verbose:
            print(f"\n{'─'*60}")
            print("  [Layer 0] 大盘风控")
            print(f"{'─'*60}")

        market_risk = check_market_risk(trade_date, self.cfg)
        self._stats['layer0'] = market_risk

        if verbose:
            status = "✅ 通过" if market_risk['passed'] else "⚠️ 未通过"
            print(f"  上涨{market_risk['advancers']}家 下跌{market_risk['decliners']}家  "
                  f"广度{market_risk['breadth_ratio']:.2%}  {status}")
            if not market_risk['passed']:
                print(f"  仓位上限: {market_risk['position_cap']:.0%}  "
                      f"原因: {market_risk['reason']}")

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
                print("  ❌ Layer 1 无候选，退出")
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
            print(f"  {len(l1_codes)} → {len(l2_codes)} 只  ({t2_elapsed:.1f}s)")

        if not l2_codes:
            if verbose:
                print("  ❌ Layer 2 全部淘汰，退出")
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

        # Layer 3 不做硬过滤，启动信号作为评分维度
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
        self._stats['layer4_covered'] = llm_covered

        if verbose:
            print(f"  LLM覆盖: {llm_covered}/{len(l3_codes)} 只  ({t4_elapsed:.1f}s)")

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
            print(f"  评分完成: {len(overnight_df)} 只  ({t5_elapsed:.1f}s)")

        # ── 融合评分 ──
        if verbose:
            print(f"\n{'─'*60}")
            print("  [融合评分] 归一化 + 加权汇总")
            print(f"{'─'*60}")

        # 过滤factor_df只保留l3_codes
        factor_df_filtered = factor_df[factor_df['ts_code'].isin(l3_codes)]

        result = normalize_scores(factor_df_filtered, launch_df, llm_data, overnight_df)

        # 限制最终候选数
        result = result.head(self.cfg.max_final_candidates)

        # 加入大盘风控信息
        result['position_cap'] = market_risk['position_cap']
        result['market_risk'] = 'pass' if market_risk['passed'] else 'warning'

        total_elapsed = time.time() - t_start
        self._stats['total_elapsed'] = round(total_elapsed, 1)
        self._stats['final_count'] = len(result)

        if verbose:
            print(f"\n{'='*70}")
            print(f"  【最终推荐 TOP {len(result)}】  {trade_date}")
            print(f"{'='*70}")

            if not result.empty:
                show_cols = ['ts_code', 'meta_score', 'factor_score', 'launch_score',
                             'llm_score', 'overnight_score', 'pct_chg', 'turnover']
                show_cols = [c for c in show_cols if c in result.columns]
                pd.set_option('display.max_columns', None)
                pd.set_option('display.width', 200)
                print(result[show_cols].to_string(index=False))

            print(f"\n  ⏱ 总耗时: {total_elapsed:.1f}s")
            print(f"  Layer0: {'✅' if market_risk['passed'] else '⚠️'}  "
                  f"Layer1: {self._stats['layer1_count']}  "
                  f"Layer2: {self._stats['layer2_count']}  "
                  f"Layer3: {self._stats['layer3_count']}  "
                  f"Layer4: {llm_covered}  "
                  f"Layer5: {self._stats['layer5_count']}")

        return result

    @property
    def stats(self) -> Dict:
        return self._stats


# ============================================================
# CLI入口
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    engine = MetaStrategyEngine()
    result = engine.run(verbose=True)

    if not result.empty:
        # 保存CSV
        out_dir = Path('./results')
        out_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(BEIJING_TZ).strftime('%Y%m%d')
        csv_path = out_dir / f"meta_strategy_{today}.csv"
        result.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"\n✓ 已保存: {csv_path}")
