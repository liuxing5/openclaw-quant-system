

# ============================================================
# 七层漏斗
# ============================================================

def layer0_market_risk(trade_date: date, cfg: MetaBacktestConfig) -> Dict:
    """Layer 0: 大盘风控 + 市场状态识别"""
    if not cfg.layer0_enabled:
        return {'passed': True, 'position_cap': 1.0, 'regime': 'oscillate',
                'regime_score': 0.0, 'weights': cfg.weights_oscillate,
                'reason': 'Layer0禁用'}

    overview = get_market_overview(trade_date)
    ratio = overview['breadth_ratio']
    passed = ratio >= cfg.layer0_min_advancers_ratio

    # 市场状态识别
    regime = 'oscillate'
    regime_score = 0.0
    try:
        start = trade_date - timedelta(days=cfg.layer0_regime_lookback + 30)
        df_idx = get_daily_quotes_cached('sh.000001', start, trade_date,
                                          fields="date,close")
        if not df_idx.empty and len(df_idx) >= 20:
            closes = df_idx['close'].values.astype(float)
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
    except Exception as e:
        logger.debug(f"市场状态识别失败: {e}")

    if regime == 'bull':
        weights = cfg.weights_bull
    elif regime == 'bear':
        weights = cfg.weights_bear
    else:
        weights = cfg.weights_oscillate

    position_cap = 1.0 if passed else 0.5
    if regime == 'bear' and position_cap > 0.3:
        position_cap = 0.3

    return {
        'passed': passed,
        'position_cap': position_cap,
        'advancers': overview['advancers'],
        'decliners': overview['decliners'],
        'breadth_ratio': round(ratio, 4),
        'regime': regime,
        'regime_score': round(regime_score, 3),
        'weights': weights,
        'reason': '' if passed else f'上涨占比{ratio:.1%}<{cfg.layer0_min_advancers_ratio:.0%}',
    }


def layer1_multi_factor_scan(trade_date: date, cfg: MetaBacktestConfig,
                              stock_pool: List[str] = None) -> pd.DataFrame:
    """Layer 1: 多因子全市场扫描 v2.0（含行业轮动）"""
    if not cfg.layer1_enabled:
        return pd.DataFrame()

    if stock_pool is None:
        stock_pool = get_active_stocks(trade_date, min_amount=cfg.layer2_min_amount)

    if not stock_pool:
        return pd.DataFrame()

    start_date = trade_date - timedelta(days=120)

    # ── 阶段1: 快速预筛 ──
    quick_start = trade_date - timedelta(days=10)
    prefiltered = []

    for ts_code in stock_pool:
        try:
            df = get_daily_quotes_cached(ts_code, quick_start, trade_date,
                                          fields="date,open,high,low,close,volume,amount,pctChg")
            if df.empty or len(df) < 3:
                continue
            close = df['close'].values.astype(float)
            pct_chg = df['pct_chg'].values.astype(float) if 'pct_chg' in df.columns else None
            if close[-1] <= 0:
                continue
            if pct_chg is not None and len(pct_chg) > 0 and float(pct_chg[-1]) < -5:
                continue
            prefiltered.append(ts_code)
        except Exception:
            pass

    logger.info(f"Layer1 预筛: {len(stock_pool)} -> {len(prefiltered)} 只")

    # ── 阶段2: 精细因子计算 ──
    results = []
    for i, ts_code in enumerate(prefiltered):
        try:
            df = get_daily_quotes_cached(ts_code, start_date, trade_date)
            if df.empty or len(df) < 30:
                continue
            close = df['close'].values.astype(float)
            high = df['high'].values.astype(float)
            low = df['low'].values.astype(float)
            amount = df['amount'].values.astype(float) if 'amount' in df.columns else np.zeros(len(df))
            if close[-1] <= 0:
                continue

            factors = compute_factors(close, high, low, amount)
            if factors['total_score'] >= cfg.layer1_min_total_score:
                factors['ts_code'] = ts_code
                factors['close'] = round(float(close[-1]), 2)
                results.append(factors)
        except Exception as e:
            logger.debug(f"Layer1 {ts_code} 计算失败: {e}")
        if (i + 1) % 30 == 0:
            time.sleep(0.1)

    if not results:
        return pd.DataFrame()

    df_result = pd.DataFrame(results)
    df_result = df_result.sort_values('total_score', ascending=False)
    if len(df_result) > cfg.layer1_top_n:
        df_result = df_result.head(cfg.layer1_top_n)
    return df_result.reset_index(drop=True)


def layer2_fundamental_filter(stock_list: List[str], trade_date: date,
                               cfg: MetaBacktestConfig) -> List[str]:
    """Layer 2: 基本面+流动性过滤"""
    if not cfg.layer2_enabled:
        return stock_list

    passed = []
    year = trade_date.year - (1 if trade_date.month < 5 else 0)
    quarter = 4 if trade_date.month < 5 else (trade_date.month - 1) // 3

    for ts_code in stock_list:
        try:
            fund = get_fundamental_data(ts_code, year, quarter)
            debt_ratio = fund.get('debt_ratio')
            if debt_ratio is not None and debt_ratio > cfg.layer2_max_debt_ratio:
                continue
            current_ratio = fund.get('current_ratio')
            if current_ratio is not None and current_ratio < cfg.layer2_min_current_ratio:
                continue
            net_margin = fund.get('net_margin')
            if net_margin is not None and net_margin < -10:
                continue
            passed.append(ts_code)
        except Exception:
            passed.append(ts_code)

    return passed


def layer3_launch_signals(stock_list: List[str], trade_date: date,
                           cfg: MetaBacktestConfig) -> pd.DataFrame:
    """Layer 3: 启动信号识别 v2.0（含封单质量+主力资金代理）"""
    if not cfg.layer3_enabled:
        return pd.DataFrame({'ts_code': stock_list, 'launch_score': [0.5] * len(stock_list)})

    start_date = trade_date - timedelta(days=120)
    results = []

    for ts_code in stock_list:
        try:
            df = get_daily_quotes_cached(ts_code, start_date, trade_date)
            if df.empty or len(df) < 20:
                continue

            close = df['close'].values.astype(float)
            amount = df['amount'].values.astype(float) if 'amount' in df.columns else np.zeros(len(df))
            pct_chg = df['pct_chg'].values.astype(float) if 'pct_chg' in df.columns else np.zeros(len(df))
            turnover = df['turnover_rate'].values.astype(float) if 'turnover_rate' in df.columns else np.zeros(len(df))
            n = len(close)

            launch_score = 0.0
            signals = []

            # 1. 放量突破
            if n >= 20:
                vol_ma20 = amount[-21:-1].mean()
                if vol_ma20 > 0:
                    vol_ratio = amount[-1] / vol_ma20
                    if vol_ratio >= cfg.layer3_volume_breakout_mult:
                        launch_score += 0.30
                        signals.append(f'放量{vol_ratio:.1f}倍')
                    elif vol_ratio >= cfg.layer3_volume_breakout_mult * 0.7:
                        launch_score += 0.15

            # 2. 价格突破
            if n >= 20:
                high_20 = close[-21:-1].max()
                if close[-1] > high_20 * (1 + cfg.layer3_price_breakout_pct):
                    launch_score += 0.25
                    signals.append('突破20日高点')

            # 3. MACD金叉
            if n >= 35:
                dif = _ema(close, 12) - _ema(close, 26)
                dea = _ema(dif, 9)
                if len(dif) >= 2 and dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
                    launch_score += 0.15
                    signals.append('MACD金叉')

            # 4. 主力资金代理 (v2.0)
            if n >= 1:
                last_pct = float(pct_chg[-1]) if len(pct_chg) > 0 else 0
                last_turnover = float(turnover[-1]) if len(turnover) > 0 else 0
                if last_turnover > 5 and last_pct > 0 and n >= 20:
                    vol_ma = amount[-21:-1].mean()
                    if vol_ma > 0 and amount[-1] > vol_ma * 1.5:
                        main_force_score = min(1.0, (last_pct / 5.0) * (last_turnover / 8.0))
                        if main_force_score >= 0.05:
                            launch_score += 0.15
                            signals.append(f'主力资金{main_force_score:.2f}')

            # 5. 封单质量 (v2.0)
            if n >= 1:
                last_pct = float(pct_chg[-1]) if len(pct_chg) > 0 else 0
                last_turnover = float(turnover[-1]) if len(turnover) > 0 else 0
                seal_quality = 0.0
                if 7.0 <= last_pct < 9.8:
                    seal_quality = (0.8 if 3.0 <= last_turnover <= 15.0
                                    else (0.5 if last_turnover > 15.0 else 0.3))
                elif last_pct >= 9.8:
                    seal_quality = (1.0 if last_turnover < 5.0
                                    else (0.9 if last_turnover < 10.0 else 0.6))
                if seal_quality >= cfg.layer3_seal_quality_min:
                    launch_score += 0.15
                    signals.append(f'封单{seal_quality:.1f}')

            if launch_score >= cfg.layer3_min_launch_score:
                results.append({
                    'ts_code': ts_code,
                    'launch_score': round(min(launch_score, 1.0), 3),
                    'launch_signals': ', '.join(signals) if signals else '',
                })
        except Exception as e:
            logger.debug(f"Layer3 {ts_code} 失败: {e}")

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)


def layer4_llm_boost(stock_list: List[str], trade_date: date,
                      cfg: MetaBacktestConfig) -> Dict[str, Dict]:
    """Layer 4: LLM事件驱动加成 v2.0（回测中用基本面代理，含否决机制）"""
    if not cfg.layer4_enabled:
        return {code: {'llm_bonus': 0.0, 'llm_veto': False, 'veto_reason': ''}
                for code in stock_list}

    year = trade_date.year - (1 if trade_date.month < 5 else 0)
    quarter = 4 if trade_date.month < 5 else (trade_date.month - 1) // 3

    boosts = {}
    for ts_code in stock_list:
        try:
            fund = get_fundamental_data(ts_code, year, quarter)
            roe = fund.get('roe') or 0
            rev_yoy = fund.get('revenue_yoy') or 0
            net_margin = fund.get('net_margin') or 0
            bonus = 0
            if roe and roe > 15:
                bonus += 5
            if rev_yoy and rev_yoy > 20:
                bonus += 5

            # 否决机制（代理：净利率极低或亏损严重）
            llm_veto = False
            veto_reason = ''
            if net_margin < -20:
                llm_veto = True
                veto_reason = 'LLM否决: 严重亏损'
                bonus = 0

            boosts[ts_code] = {
                'llm_bonus': min(bonus, 15),
                'llm_veto': llm_veto,
                'veto_reason': veto_reason,
            }
        except Exception:
            boosts[ts_code] = {'llm_bonus': 0.0, 'llm_veto': False, 'veto_reason': ''}

    return boosts


def layer5_overnight_score(stock_list: List[str], trade_date: date,
                            cfg: MetaBacktestConfig) -> pd.DataFrame:
    """Layer 5: 八步法精细评分 v2.0（双池分治+情绪感知+行业评分）"""
    if not cfg.layer5_enabled:
        return pd.DataFrame({'ts_code': stock_list,
                             'overnight_score': [50] * len(stock_list),
                             'pool': ['stable'] * len(stock_list)})

    start_date = trade_date - timedelta(days=60)
    results = []

    # 情绪感知
    sentiment_score = 0.0
    if cfg.layer5_sentiment_enabled:
        overview = get_market_overview(trade_date)
        if overview['breadth_ratio'] > 0.6:
            sentiment_score = 5.0
        elif overview['breadth_ratio'] > 0.5:
            sentiment_score = 2.5

    for ts_code in stock_list:
        try:
            df = get_daily_quotes_cached(ts_code, start_date, trade_date)
            if df.empty or len(df) < 5:
                continue

            close = df['close'].values.astype(float)
            amount = df['amount'].values.astype(float) if 'amount' in df.columns else np.zeros(len(df))
            pct_chg = df['pct_chg'].values.astype(float) if 'pct_chg' in df.columns else np.zeros(len(df))
            n = len(close)

            score = 0.0

            # 双池分类
            last_pct = float(pct_chg[-1]) if len(pct_chg) > 0 else 0
            if last_pct <= cfg.layer5_stable_pool_pct_max:
                pool = 'stable'
            elif last_pct <= cfg.layer5_upper_pool_pct_max:
                pool = 'upper'
            else:
                pool = 'extreme'

            # 涨幅评分（双池差异化）
            if pool == 'stable':
                if 2.0 <= last_pct <= 4.0:
                    score += 30
                elif 4.0 < last_pct <= 5.0:
                    score += 25
                elif 0 < last_pct < 2.0:
                    score += 15
            elif pool == 'upper':
                if 5.0 < last_pct <= 7.0:
                    score += 22
                elif 7.0 < last_pct <= 9.5:
                    score += 12

            # 量比评分
            if n >= 20:
                vol_mean = amount[-21:-1].mean()
                if vol_mean > 0:
                    vr = amount[-1] / vol_mean
                    if cfg.layer5_vol_ratio_min <= vr <= 10:
                        score += 25
                    elif vr > 1:
                        score += 10

            # MA5距离评分
            if n >= 5:
                ma5 = close[-5:].mean()
                dist = (close[-1] - ma5) / (ma5 + 1e-9)
                if 0 <= dist <= 0.03:
                    score += 20
                elif dist < 0:
                    score += 5

            # 连涨天数评分
            if n >= 3:
                up_days = sum(1 for i in range(-3, 0) if pct_chg[i] > 0)
                if up_days >= 2:
                    score += 15

            # 换手率评分
            if 'turnover_rate' in df.columns:
                tr = float(df['turnover_rate'].iloc[-1])
                if 3 <= tr <= 15:
                    score += 10

            # 情绪加成
            score += sentiment_score

            # 高位风险扣分
            if pool == 'upper' and last_pct > 8.0:
                score -= 10
            elif pool == 'extreme':
                score -= 20

            results.append({
                'ts_code': ts_code,
                'overnight_score': min(score, 120),
                'pool': pool,
                'pct_chg': round(last_pct, 2),
            })
        except Exception as e:
            logger.debug(f"Layer5 {ts_code} 失败: {e}")

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)


def layer6_sustain_eval(stock_list: List[str], trade_date: date,
                         cfg: MetaBacktestConfig) -> pd.DataFrame:
    """Layer 6: 持续性评估 v2.0（ADX趋势+连涨+量能+均线）"""
    if not cfg.layer6_enabled:
        return pd.DataFrame({'ts_code': stock_list, 'sustain_score': [0.5] * len(stock_list)})

    start_date = trade_date - timedelta(days=120)
    results = []

    for ts_code in stock_list:
        try:
            df = get_daily_quotes_cached(ts_code, start_date, trade_date)
            if df.empty or len(df) < 30:
                continue

            close = df['close'].values.astype(float)
            high = df['high'].values.astype(float)
            low = df['low'].values.astype(float)
            amount = df['amount'].values.astype(float) if 'amount' in df.columns else np.zeros(len(df))
            pct_chg = df['pct_chg'].values.astype(float) if 'pct_chg' in df.columns else np.zeros(len(df))
            n = len(close)

            sustain_score = 0.0

            # ADX趋势强度
            p = 14
            tr = np.maximum(high[1:] - low[1:],
                            np.maximum(np.abs(high[1:] - close[:-1]),
                                       np.abs(low[1:] - close[:-1])))
            up = high[1:] - high[:-1]; dn = low[:-1] - low[1:]
            atr = _ema(tr, p)
            pdi = 100 * _ema(np.where((up > dn) & (up > 0), up, 0.0), p) / (atr + 1e-9)
            mdi = 100 * _ema(np.where((dn > up) & (dn > 0), dn, 0.0), p) / (atr + 1e-9)
            adx_arr = _ema(100 * np.abs(pdi - mdi) / (pdi + mdi + 1e-9), p)
            adx_val = float(adx_arr[-1])
            pdi_val = float(pdi[-1]); mdi_val = float(mdi[-1])

            if adx_val >= cfg.layer6_adx_trend_min and pdi_val > mdi_val:
                sustain_score += 0.30
            elif pdi_val > mdi_val:
                sustain_score += 0.15

            # 连涨天数
            consecutive_up = 0
            for i in range(-1, -min(n, 15), -1):
                if pct_chg[i] > 0:
                    consecutive_up += 1
                else:
                    break
            if consecutive_up <= 3:
                sustain_score += 0.15
            elif consecutive_up <= 7:
                sustain_score += 0.08
            else:
                sustain_score -= 0.10

            # 量能配合
            if n >= 10:
                up_mask = pct_chg[-10:] > 0
                down_mask = pct_chg[-10:] < 0
                up_vol = amount[-10:][up_mask].mean() if up_mask.any() else 0
                down_vol = amount[-10:][down_mask].mean() if down_mask.any() else 1e-9
                if up_vol > down_vol * 1.2:
                    sustain_score += 0.15
                elif up_vol > down_vol:
                    sustain_score += 0.08
                else:
                    sustain_score -= 0.05

            # 均线支撑
            if n >= 20:
                ma5 = close[-6:-1].mean() if n >= 6 else close.mean()
                ma10 = close[-11:-1].mean() if n >= 11 else close.mean()
                ma20 = close[-21:-1].mean() if n >= 21 else close.mean()
                last_close = float(close[-1])
                ma_support = sum(1 for m in [ma5, ma10, ma20] if last_close > m)
                if ma_support == 3:
                    sustain_score += 0.10
                elif ma_support >= 2:
                    sustain_score += 0.05
                else:
                    sustain_score -= 0.05

            sustain_score = max(0, min(1, sustain_score))
            results.append({
                'ts_code': ts_code,
                'sustain_score': round(sustain_score, 3),
            })
        except Exception as e:
            logger.debug(f"Layer6 {ts_code} 失败: {e}")

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)
