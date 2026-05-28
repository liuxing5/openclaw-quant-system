

# ============================================================
# 融合评分归一化 v2.0（动态权重+持续性调节+LLM否决）
# ============================================================

def normalize_and_fuse(factor_df: pd.DataFrame, launch_df: pd.DataFrame,
                       llm_data: Dict[str, Dict], overnight_df: pd.DataFrame,
                       sustain_df: pd.DataFrame = None,
                       weights: Dict[str, float] = None) -> pd.DataFrame:
    """统一评分归一化 v2.0"""
    if factor_df.empty:
        return pd.DataFrame()

    if weights is None:
        weights = {'factor': 0.25, 'launch': 0.15, 'llm': 0.20, 'overnight': 0.40}

    merged = factor_df[['ts_code', 'total_score', 'close']].copy()
    merged = merged.rename(columns={'total_score': 'factor_raw'})

    # 归一化因子分到0-100
    if merged['factor_raw'].max() > merged['factor_raw'].min():
        merged['factor_score'] = (
            (merged['factor_raw'] - merged['factor_raw'].min()) /
            (merged['factor_raw'].max() - merged['factor_raw'].min()) * 100
        ).round(2)
    else:
        merged['factor_score'] = 50.0

    # launch_score
    if not launch_df.empty and 'launch_score' in launch_df.columns:
        launch_map = dict(zip(launch_df['ts_code'], launch_df['launch_score']))
        merged['launch_score'] = merged['ts_code'].map(launch_map).fillna(0)
        merged['launch_score'] = (merged['launch_score'] * 100).round(2)
    else:
        merged['launch_score'] = 0

    # llm_score + veto
    merged['llm_score'] = merged['ts_code'].map(
        lambda c: llm_data.get(c, {}).get('llm_bonus', 0)).fillna(0).round(2)
    merged['llm_veto'] = merged['ts_code'].map(
        lambda c: llm_data.get(c, {}).get('llm_veto', False)).fillna(False)

    # overnight_score
    if not overnight_df.empty and 'overnight_score' in overnight_df.columns:
        ov_map = dict(zip(overnight_df['ts_code'], overnight_df['overnight_score']))
        merged['overnight_score'] = merged['ts_code'].map(ov_map).fillna(0).round(2)
        pool_map = dict(zip(overnight_df['ts_code'], overnight_df.get('pool', pd.Series())))
        merged['pool'] = merged['ts_code'].map(pool_map).fillna('stable')
    else:
        merged['overnight_score'] = 0
        merged['pool'] = 'stable'

    # sustain_score
    if sustain_df is not None and not sustain_df.empty and 'sustain_score' in sustain_df.columns:
        merged['sustain_raw'] = merged['ts_code'].map(
            dict(zip(sustain_df['ts_code'], sustain_df['sustain_score']))
        ).fillna(0.5)
    else:
        merged['sustain_raw'] = 0.5

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

    # 只保留有启动信号或隔夜评分的
    merged = merged[(merged['launch_score'] > 0) | (merged['overnight_score'] > 0)]

    return merged.sort_values('meta_score', ascending=False).reset_index(drop=True)


# ============================================================
# 策略对比回测
# ============================================================

def run_single_strategy_backtest(strategy_name: str, trading_days: List[date],
                                  cfg: MetaBacktestConfig) -> Dict:
    """运行单个策略的回测用于对比"""
    capital = cfg.initial_capital
    all_trades = []
    max_positions = cfg.max_positions
    position_value = cfg.initial_capital * cfg.single_position_pct

    positions: Dict[str, Dict] = {}

    for i, trade_date in enumerate(trading_days):
        # 评估退出
        for ts_code in list(positions.keys()):
            pos = positions[ts_code]
            holding_days = (trade_date - pos['entry_date']).days
            # 简单退出：硬止损8% + 时间止损15天
            start = trade_date - timedelta(days=5)
            df = get_daily_quotes_cached(ts_code, start, trade_date, fields="date,close")
            if df.empty:
                continue
            current_price = float(df['close'].iloc[-1])
            pnl_pct = (current_price - pos['entry_price']) / pos['entry_price']

            should_exit = False
            exit_reason = ''
            if pnl_pct <= -0.08:
                should_exit = True; exit_reason = '硬止损'
            elif holding_days >= 15:
                should_exit = True; exit_reason = '时间止损'

            if should_exit:
                shares = pos['shares']
                exit_price = current_price * (1 - cfg.slippage_pct)
                commission = exit_price * shares * cfg.commission_rate
                capital += exit_price * shares - commission
                all_trades.append({
                    'ts_code': ts_code,
                    'entry_date': str(pos['entry_date']),
                    'exit_date': str(trade_date),
                    'entry_price': pos['entry_price'],
                    'exit_price': exit_price,
                    'pnl_pct': round(pnl_pct, 4),
                    'holding_days': holding_days,
                    'exit_reason': exit_reason,
                })
                del positions[ts_code]

        # 生成信号
        if strategy_name == 'multi_factor_only':
            factor_df = layer1_multi_factor_scan(trade_date, cfg)
            candidates = factor_df['ts_code'].tolist() if not factor_df.empty else []
        elif strategy_name == 'overnight_only':
            stock_pool = get_active_stocks(trade_date, min_amount=cfg.layer2_min_amount)
            overnight_df = layer5_overnight_score(stock_pool, trade_date, cfg)
            candidates = overnight_df.nlargest(10, 'overnight_score')['ts_code'].tolist() if not overnight_df.empty else []
        elif strategy_name == 'no_layer0':
            factor_df = layer1_multi_factor_scan(trade_date, cfg)
            l1_codes = factor_df['ts_code'].tolist() if not factor_df.empty else []
            if not l1_codes:
                continue
            l2_codes = layer2_fundamental_filter(l1_codes, trade_date, cfg)
            launch_df = layer3_launch_signals(l2_codes, trade_date, cfg)
            llm_data = layer4_llm_boost(l2_codes, trade_date, cfg)
            overnight_df = layer5_overnight_score(l2_codes, trade_date, cfg)
            result_df = normalize_and_fuse(factor_df, launch_df, llm_data, overnight_df)
            candidates = result_df['ts_code'].tolist() if not result_df.empty else []
        else:
            candidates = []

        # 开仓
        for ts_code in candidates:
            if len(positions) >= max_positions:
                break
            if ts_code in positions:
                continue
            next_idx = i + 1
            if next_idx >= len(trading_days):
                continue
            next_date = trading_days[next_idx]
            entry_price = _get_open_price_simple(ts_code, next_date)
            if entry_price is None or entry_price <= 0:
                continue
            shares = int(position_value / (entry_price * 100)) * 100
            if shares <= 0:
                shares = 100
            entry_price_adj = entry_price * (1 + cfg.slippage_pct)
            commission = entry_price_adj * shares * cfg.commission_rate
            cost = entry_price_adj * shares + commission
            if cost > capital:
                continue
            capital -= cost
            positions[ts_code] = {
                'entry_date': next_date,
                'entry_price': entry_price_adj,
                'shares': shares,
            }

    # 强制平仓
    for ts_code in list(positions.keys()):
        pos = positions[ts_code]
        start = trading_days[-1] - timedelta(days=5) if trading_days else pos['entry_date']
        df = get_daily_quotes_cached(ts_code, start, trading_days[-1], fields="date,close")
        if not df.empty:
            current_price = float(df['close'].iloc[-1])
            pnl_pct = (current_price - pos['entry_price']) / pos['entry_price']
            all_trades.append({
                'ts_code': ts_code,
                'entry_date': str(pos['entry_date']),
                'exit_date': str(trading_days[-1]),
                'entry_price': pos['entry_price'],
                'exit_price': current_price,
                'pnl_pct': round(pnl_pct, 4),
                'holding_days': (trading_days[-1] - pos['entry_date']).days,
                'exit_reason': '回测结束',
            })

    return {'trades': all_trades, 'strategy': strategy_name}


def _get_open_price_simple(ts_code: str, trade_date: date) -> Optional[float]:
    """简单获取开盘价"""
    start = trade_date - timedelta(days=5)
    df = get_daily_quotes(ts_code, start, trade_date, fields="date,open")
    if df.empty:
        return None
    last = df.iloc[-1]
    return float(last['open']) if pd.notna(last['open']) else None


# ============================================================
# 回测主类
# ============================================================

class BaostockBacktester:
    """基于 Baostock 的融合元策略回测器 v2.0"""

    def __init__(self, cfg: MetaBacktestConfig = None,
                 pm_cfg: PositionManagerConfig = None):
        self.cfg = cfg or DEFAULT_BT_CONFIG
        self.pm_cfg = pm_cfg or DEFAULT_PM_CONFIG

    def run(self) -> Dict:
        """运行回测 v2.0"""
        logger.info("=" * 70)
        logger.info(f"融合元策略回测 v2.0 (Baostock) {self.cfg.start_date} ~ {self.cfg.end_date}")
        logger.info("=" * 70)

        ensure_login()

        start_date = date.fromisoformat(self.cfg.start_date)
        end_date = date.fromisoformat(self.cfg.end_date)

        trading_days = get_trading_days(start_date, end_date)
        if not trading_days:
            logger.error("无交易日数据")
            logout()
            return {}

        logger.info(f"交易日: {len(trading_days)} 天")

        pm = PositionManager(self.pm_cfg)
        pm.cfg.max_positions = self.cfg.max_positions

        capital = self.cfg.initial_capital
        daily_equity = []
        all_trades = []
        layer_stats = {
            'L0_reject': 0, 'L0_regime': {'bull': 0, 'oscillate': 0, 'bear': 0},
            'L1_count': [], 'L2_count': [], 'L3_count': [],
            'L4_covered': [], 'L4_vetoed': 0,
            'L5_count': [], 'L5_pool': {'stable': 0, 'upper': 0, 'extreme': 0},
            'L6_count': [], 'final_count': [],
        }

        t_start = time.time()

        for i, trade_date in enumerate(trading_days):
            try:
                # ── 1. 评估退出条件 ──
                self._update_position_prices(pm, trade_date)
                exit_signals = pm.evaluate_exits(trade_date)

                for sig in exit_signals:
                    exit_price = self._get_open_price(sig.ts_code, trade_date)
                    if exit_price is None:
                        exit_price = sig.current_price

                    exit_price_adj = exit_price * (1 - self.cfg.slippage_pct)
                    pos = pm.positions.get(sig.ts_code)
                    shares = pos.shares if pos else 100
                    commission = exit_price_adj * shares * self.cfg.commission_rate

                    record = pm.close_position(
                        sig.ts_code, trade_date, exit_price_adj, sig.exit_reason)
                    if record:
                        record['commission'] = commission
                        all_trades.append(record)
                        capital += exit_price_adj * shares - commission

                # ── 2. Layer 0: 大盘风控 + 市场状态 ──
                market_risk = layer0_market_risk(trade_date, self.cfg)
                regime = market_risk.get('regime', 'oscillate')
                layer_stats['L0_regime'][regime] = layer_stats['L0_regime'].get(regime, 0) + 1
                weights = market_risk.get('weights', self.cfg.weights_oscillate)

                if not market_risk['passed']:
                    layer_stats['L0_reject'] += 1
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count,
                                         'regime': regime})
                    continue

                # ── 3. Layer 1: 多因子扫描 ──
                factor_df = layer1_multi_factor_scan(trade_date, self.cfg)
                l1_codes = factor_df['ts_code'].tolist() if not factor_df.empty else []
                layer_stats['L1_count'].append(len(l1_codes))

                if not l1_codes:
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count,
                                         'regime': regime})
                    continue

                # ── 4. Layer 2: 基本面过滤 ──
                l2_codes = layer2_fundamental_filter(l1_codes, trade_date, self.cfg)
                layer_stats['L2_count'].append(len(l2_codes))

                if not l2_codes:
                    equity = self._calc_equity(pm, capital, trade_date)
                    daily_equity.append({'date': str(trade_date), 'equity': equity,
                                         'positions': pm.open_position_count,
                                         'regime': regime})
                    continue

                # ── 5. Layer 3: 启动信号 ──
                launch_df = layer3_launch_signals(l2_codes, trade_date, self.cfg)
                l3_codes = launch_df['ts_code'].tolist() if not launch_df.empty else []
                layer_stats['L3_count'].append(len(l3_codes))

                # ── 6. Layer 4: LLM加成 ──
                llm_data = layer4_llm_boost(l2_codes, trade_date, self.cfg)
                l4_covered = sum(1 for v in llm_data.values() if v.get('llm_bonus', 0) > 0)
                l4_vetoed = sum(1 for v in llm_data.values() if v.get('llm_veto', False))
                layer_stats['L4_covered'].append(l4_covered)
                layer_stats['L4_vetoed'] += l4_vetoed

                # ── 7. Layer 5: 八步法评分 ──
                overnight_df = layer5_overnight_score(l2_codes, trade_date, self.cfg)
                l5_codes = overnight_df['ts_code'].tolist() if not overnight_df.empty else []
                layer_stats['L5_count'].append(len(l5_codes))
                if not overnight_df.empty and 'pool' in overnight_df.columns:
                    for pool_type in ['stable', 'upper', 'extreme']:
                        count = (overnight_df['pool'] == pool_type).sum()
                        layer_stats['L5_pool'][pool_type] += count

                # ── 8. Layer 6: 持续性评估 ──
                sustain_df = layer6_sustain_eval(l2_codes, trade_date, self.cfg)
                layer_stats['L6_count'].append(len(sustain_df))

                # ── 9. 融合评分 ──
                result_df = normalize_and_fuse(
                    factor_df, launch_df, llm_data, overnight_df,
                    sustain_df=sustain_df, weights=weights)
                layer_stats['final_count'].append(len(result_df))

                # LLM否决过滤
                vetoed_codes = set()
                for ts_code, data in llm_data.items():
                    if data.get('llm_veto', False):
                        vetoed_codes.add(ts_code)
                if vetoed_codes:
                    result_df = result_df[~result_df['ts_code'].isin(vetoed_codes)]

                # ── 10. 开仓 ──
                if not result_df.empty:
                    for _, row in result_df.iterrows():
                        if pm.open_position_count >= self.cfg.max_positions:
                            break
                        if row['ts_code'] in pm.positions:
                            continue

                        next_idx = i + 1
                        if next_idx >= len(trading_days):
                            continue
                        next_date = trading_days[next_idx]
                        entry_price = self._get_open_price(row['ts_code'], next_date)
                        if entry_price is None or entry_price <= 0:
                            continue

                        # 仓位（根据市场状态调整）
                        position_pct = self.cfg.single_position_pct * market_risk.get('position_cap', 1.0)
                        position_value = self.cfg.initial_capital * position_pct
                        shares = int(position_value / (entry_price * 100)) * 100
                        if shares <= 0:
                            shares = 100

                        entry_price_adj = entry_price * (1 + self.cfg.slippage_pct)
                        commission = entry_price_adj * shares * self.cfg.commission_rate
                        cost = entry_price_adj * shares + commission

                        if cost > capital:
                            continue

                        capital -= cost
                        pm.positions[row['ts_code']] = Position(
                            ts_code=row['ts_code'],
                            entry_date=next_date,
                            entry_price=entry_price_adj,
                            shares=shares,
                            meta_score=row.get('meta_score', 0),
                            launch_score=row.get('launch_score', 0),
                            factor_score=row.get('factor_score', 0),
                        )

                # ── 11. 记录权益 ──
                equity = self._calc_equity(pm, capital, trade_date)
                daily_equity.append({'date': str(trade_date), 'equity': equity,
                                     'positions': pm.open_position_count,
                                     'regime': regime})

                # 进度
                if (i + 1) % 5 == 0 or i == len(trading_days) - 1:
                    elapsed = time.time() - t_start
                    avg = elapsed / (i + 1)
                    remaining = avg * (len(trading_days) - i - 1)
                    logger.info(
                        f"进度 {i+1}/{len(trading_days)} ({trade_date}): "
                        f"持仓{pm.open_position_count}只 权益{equity:,.0f} "
                        f"市场{regime} 剩余{remaining:.0f}s")

                # 定期清理缓存
                if (i + 1) % 10 == 0:
                    clear_cache()

            except Exception as e:
                import traceback
                logger.warning(f"{trade_date} 回测失败: {e}")
                logger.debug(traceback.format_exc())

        # 强制平仓
        for ts_code in list(pm.positions.keys()):
            pos = pm.positions[ts_code]
            last_price = self._get_close_price(ts_code, end_date)
            if last_price:
                record = pm.close_position(ts_code, end_date, last_price, '回测结束')
                if record:
                    all_trades.append(record)

        total_elapsed = time.time() - t_start

        # ── 策略对比回测 ──
        compare_results = {}
        if cfg.strategy_compare_enabled:
            logger.info("\n运行策略对比回测...")
            for strategy_name in ['multi_factor_only', 'overnight_only', 'no_layer0']:
                try:
                    logger.info(f"  对比策略: {strategy_name}")
                    cmp_result = run_single_strategy_backtest(
                        strategy_name, trading_days, self.cfg)
                    compare_results[strategy_name] = cmp_result
                except Exception as e:
                    logger.warning(f"  {strategy_name} 对比失败: {e}")

        logout()

        # 汇总
        summary = self._build_summary(
            all_trades, daily_equity, layer_stats, total_elapsed, compare_results)

        return {
            'trades': all_trades,
            'daily_equity': pd.DataFrame(daily_equity),
            'summary': summary,
            'layer_stats': layer_stats,
            'compare_results': compare_results,
        }

    def _update_position_prices(self, pm: PositionManager, trade_date: date):
        """更新持仓的当前价格"""
        start_date = trade_date - timedelta(days=60)
        for ts_code in list(pm.positions.keys()):
            try:
                df = get_daily_quotes_cached(ts_code, start_date, trade_date)
                if not df.empty:
                    pass
            except Exception:
                pass

    def _get_open_price(self, ts_code: str, trade_date: date) -> Optional[float]:
        """获取开盘价"""
        start = trade_date - timedelta(days=5)
        df = get_daily_quotes(ts_code, start, trade_date, fields="date,open,close")
        if df.empty:
            return None
        last = df.iloc[-1]
        return float(last['open']) if pd.notna(last['open']) else None

    def _get_close_price(self, ts_code: str, trade_date: date) -> Optional[float]:
        """获取收盘价"""
        start = trade_date - timedelta(days=5)
        df = get_daily_quotes(ts_code, start, trade_date, fields="date,close")
        if df.empty:
            return None
        last = df.iloc[-1]
        return float(last['close']) if pd.notna(last['close']) else None

    def _calc_equity(self, pm: PositionManager, cash: float,
                     eval_date: date) -> float:
        """计算总权益"""
        equity = cash
        for ts_code, pos in pm.positions.items():
            price = self._get_close_price(ts_code, eval_date)
            if price:
                equity += price * pos.shares
            else:
                equity += pos.entry_price * pos.shares
        return equity

    def _build_summary(self, trades: List[Dict], daily_equity: List[Dict],
                       layer_stats: Dict, elapsed: float,
                       compare_results: Dict = None) -> str:
        """构建回测汇总 v2.0"""
        lines = []
        lines.append("=" * 70)
        lines.append("  融合元策略回测汇总 v2.0 (Baostock)")
        lines.append("=" * 70)
        lines.append(f"  回测区间: {self.cfg.start_date} ~ {self.cfg.end_date}")
        lines.append(f"  初始资金: {self.cfg.initial_capital:,.0f}")
        lines.append(f"  回测耗时: {elapsed:.1f}s")
        lines.append("")

        if trades:
            pnls = [t['pnl_pct'] for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            lines.append("--- 交易统计 ---")
            lines.append(f"  总交易数: {len(trades)}")
            lines.append(f"  胜率: {len(wins)/len(pnls):.1%}" if pnls else "  胜率: N/A")
            lines.append(f"  平均收益: {np.mean(pnls):.2%}" if pnls else "  平均收益: N/A")
            lines.append(f"  中位数收益: {np.median(pnls):.2%}" if pnls else "  中位数: N/A")
            lines.append(f"  最大单笔盈利: {max(pnls):.2%}" if pnls else "  最大盈利: N/A")
            lines.append(f"  最大单笔亏损: {min(pnls):.2%}" if pnls else "  最大亏损: N/A")
            lines.append(f"  盈利交易平均: {np.mean(wins):.2%}" if wins else "  盈利均: N/A")
            lines.append(f"  亏损交易平均: {np.mean(losses):.2%}" if losses else "  亏损均: N/A")
            lines.append(f"  盈亏比: {abs(np.mean(wins)/np.mean(losses)):.2f}" if wins and losses else "  盈亏比: N/A")
            lines.append(f"  平均持仓天数: {np.mean([t['holding_days'] for t in trades]):.1f}")
            lines.append("")

            # 退出原因分布
            exit_reasons = {}
            for t in trades:
                reason = t['exit_reason'].split('(')[0]
                exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
            lines.append("--- 退出原因分布 ---")
            for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"  {reason}: {count} ({count/len(trades):.1%})")
            lines.append("")

            # 收益分布
            rets = np.array(pnls)
            lines.append("--- 收益分布 ---")
            lines.append(f"  >5%:  {np.mean(rets > 0.05):.1%}")
            lines.append(f"  >3%:  {np.mean(rets > 0.03):.1%}")
            lines.append(f"  >0%:  {np.mean(rets > 0):.1%}")
            lines.append(f"  <-3%: {np.mean(rets < -0.03):.1%}")
            lines.append(f"  <-5%: {np.mean(rets < -0.05):.1%}")
            lines.append(f"  <-8%: {np.mean(rets < -0.08):.1%}")
            lines.append("")
        else:
            lines.append("  无交易记录")
            lines.append("")

        # 权益曲线
        if daily_equity:
            eq_df = pd.DataFrame(daily_equity)
            initial = self.cfg.initial_capital
            final = eq_df['equity'].iloc[-1]
            total_return = (final - initial) / initial
            cummax = eq_df['equity'].cummax()
            max_dd = ((eq_df['equity'] - cummax) / cummax).min()

            lines.append("--- 权益曲线 ---")
            lines.append(f"  初始权益: {initial:,.0f}")
            lines.append(f"  最终权益: {final:,.0f}")
            lines.append(f"  总收益率: {total_return:.2%}")
            lines.append(f"  最大回撤: {max_dd:.2%}")
            lines.append("")

        # 市场状态分布
        if 'L0_regime' in layer_stats:
            lines.append("--- 市场状态分布 ---")
            total_days = sum(layer_stats['L0_regime'].values())
            for regime, count in layer_stats['L0_regime'].items():
                if count > 0:
                    lines.append(f"  {regime}: {count}天 ({count/total_days:.1%})" if total_days > 0 else f"  {regime}: {count}天")
            lines.append("")

        # 双池分布
        if 'L5_pool' in layer_stats:
            lines.append("--- 双池分布 ---")
            total_pool = sum(layer_stats['L5_pool'].values())
            for pool_type, count in layer_stats['L5_pool'].items():
                if count > 0:
                    lines.append(f"  {pool_type}: {count}只 ({count/total_pool:.1%})" if total_pool > 0 else f"  {pool_type}: {count}只")
            lines.append("")

        # 各层漏斗统计
        lines.append("--- 各层漏斗平均通过数 ---")
        for layer, counts in layer_stats.items():
            if counts and isinstance(counts, list):
                avg = np.mean(counts) if counts else 0
                lines.append(f"  {layer}: 平均 {avg:.0f} 只/日")
            elif isinstance(counts, (int, float)):
                lines.append(f"  {layer}: {counts}")
        lines.append("")

        # 策略对比
        if compare_results:
            lines.append("--- 策略对比 ---")
            lines.append(f"  {'策略':<25} {'交易数':>6} {'胜率':>8} {'平均收益':>10} {'总收益':>10}")
            lines.append(f"  {'─'*65}")

            # 融合策略
            if trades:
                pnls = [t['pnl_pct'] for t in trades]
                win_rate = len([p for p in pnls if p > 0]) / len(pnls) if pnls else 0
                avg_ret = np.mean(pnls) if pnls else 0
                total_ret = sum(pnls) if pnls else 0
                lines.append(f"  {'融合策略(v2.0)':<25} {len(trades):>6} {win_rate:>8.1%} {avg_ret:>10.2%} {total_ret:>10.2%}")

            for strategy_name, cmp in compare_results.items():
                cmp_trades = cmp.get('trades', [])
                if cmp_trades:
                    cmp_pnls = [t['pnl_pct'] for t in cmp_trades]
                    cmp_win = len([p for p in cmp_pnls if p > 0]) / len(cmp_pnls) if cmp_pnls else 0
                    cmp_avg = np.mean(cmp_pnls) if cmp_pnls else 0
                    cmp_total = sum(cmp_pnls) if cmp_pnls else 0
                    lines.append(f"  {strategy_name:<25} {len(cmp_trades):>6} {cmp_win:>8.1%} {cmp_avg:>10.2%} {cmp_total:>10.2%}")
                else:
                    lines.append(f"  {strategy_name:<25} {'0':>6} {'N/A':>8} {'N/A':>10} {'N/A':>10}")
            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)
