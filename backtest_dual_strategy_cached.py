"""
使用本地缓存数据运行双策略回测
如果缓存不存在，则尝试从数据库加载并缓存
"""
import sys
import psycopg2
import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor
import time
import pickle
import os
from datetime import date, timedelta, datetime

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"
CACHE_FILE = r'd:\pythonProject\openclaw-quant-system\data_cache.pkl'

INITIAL_CAPITAL = 1_000_000
STOP_LOSS = -0.02
MIN_LOTS = 100
MAX_PICKS_PER_DAY = 5

DIP_PCT_LO = -6.0
DIP_PCT_HI = -3.0
DIP_VOL_RATIO_MAX = 0.8
DIP_MA20_TOLERANCE = 0.05
DIP_MA5_DEVIATION = 0.03
DIP_TURN_MIN = 3.0
DIP_TURN_MAX = 15.0
DIP_MIN_AMOUNT = 30_000_000

STABILIZE_PCT_MIN = 2.0
STABILIZE_VOL_RATIO_MIN = 1.5
STABILIZE_TURN_MIN = 3.0
STABILIZE_TURN_MAX = 15.0
STABILIZE_MIN_AMOUNT = 30_000_000

MA5_WINDOW = 5
MA20_WINDOW = 20
VOL_LOOKBACK = 10

OUTPUT_FILE = r'd:\pythonProject\openclaw-quant-system\diag_key_stocks_out.txt'

_out = None

def log(msg="", end="\n"):
    global _out
    print(msg, end=end, flush=True)
    if _out:
        _out.write(msg + end)
        _out.flush()


def load_data_cached(start_date, end_date, lookback_days=30):
    """从缓存或数据库加载数据"""
    sd = datetime.strptime(start_date, "%Y-%m-%d")
    lookback_start = (sd - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    
    # 尝试从缓存加载
    if os.path.exists(CACHE_FILE):
        log(f"步骤1: 从本地缓存加载数据...")
        t0 = time.time()
        with open(CACHE_FILE, "rb") as f:
            df = pickle.load(f)
        log(f"  缓存加载完成: {len(df)} 条, {df['ts_code'].nunique()} 只, 耗时{time.time()-t0:.1f}s")
        log(f"  日期: {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")
        return df
    
    # 从数据库加载
    log(f"步骤1: 从数据库加载数据 {start_date} ~ {end_date} (前置数据从{lookback_start}开始)...")
    t0 = time.time()

    max_retries = 10
    for attempt in range(max_retries):
        conn = None
        try:
            conn = psycopg2.connect(DB_URL, connect_timeout=60)
            conn.autocommit = True
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            cur.execute("""
                SELECT ts_code, trade_date, open, high, low, close,
                       volume, amount, pct_chg, turnover_rate
                FROM daily_quotes
                WHERE trade_date >= %s AND trade_date <= %s
                  AND pct_chg IS NOT NULL
                  AND amount IS NOT NULL
                  AND turnover_rate IS NOT NULL
                ORDER BY ts_code, trade_date
            """, (lookback_start, end_date))

            rows = cur.fetchall()
            cur.close()
            conn.close()

            df = pd.DataFrame(rows)
            for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=["close", "pct_chg", "amount", "turnover_rate"])
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

            log(f"  加载完成: {len(df)} 条, {df['ts_code'].nunique()} 只, 耗时{time.time()-t0:.1f}s")
            log(f"  日期: {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")
            
            # 保存到缓存
            with open(CACHE_FILE, "wb") as f:
                pickle.dump(df, f)
            log(f"  数据已缓存到 {CACHE_FILE}")
            return df

        except psycopg2.OperationalError as e:
            if conn:
                try:
                    conn.close()
                except:
                    pass
            if attempt < max_retries - 1:
                wait = 15 + attempt * 10
                log(f"  连接失败(尝试{attempt+1}/{max_retries}): {e}，{wait}秒后重试...")
                time.sleep(wait)
            else:
                raise


def compute_indicators(df):
    log(f"  数据形状: {df.shape}, 股票数: {df['ts_code'].nunique()}")
    log("步骤2: 计算MA5/MA20...")
    t0 = time.time()
    df["ma5"] = df.groupby("ts_code")["close"].transform(
        lambda x: x.rolling(MA5_WINDOW, min_periods=MA5_WINDOW).mean().shift(1)
    )
    df["ma20"] = df.groupby("ts_code")["close"].transform(
        lambda x: x.rolling(MA20_WINDOW, min_periods=MA20_WINDOW).mean().shift(1)
    )
    log(f"  MA完成, 耗时{time.time()-t0:.1f}s")

    log("步骤3: 计算量比...")
    t0 = time.time()
    avg_vol = df.groupby("ts_code")["volume"].transform(
        lambda x: x.rolling(VOL_LOOKBACK, min_periods=5).mean().shift(1)
    )
    df["vol_ratio"] = df["volume"] / avg_vol
    log(f"  量比完成, 耗时{time.time()-t0:.1f}s")

    log("步骤4: 计算连续下跌天数...")
    t0 = time.time()

    def calc_down_days(pct_series):
        down_days = [0] * len(pct_series)
        for i in range(len(pct_series)):
            if pct_series.iloc[i] < 0:
                down_days[i] = down_days[i - 1] + 1 if i > 0 else 1
            else:
                down_days[i] = 0
        return pd.Series(down_days, index=pct_series.index)

    df["down_days"] = df.groupby("ts_code")["pct_chg"].transform(calc_down_days)
    log(f"  连续下跌完成, 耗时{time.time()-t0:.1f}s")

    log("步骤5: 计算连续不创新低...")
    t0 = time.time()

    def calc_no_new_low(low_series):
        no_new_low = [0] * len(low_series)
        for i in range(1, len(low_series)):
            if low_series.iloc[i] >= low_series.iloc[i-1]:
                no_new_low[i] = no_new_low[i-1] + 1
            else:
                no_new_low[i] = 0
        return pd.Series(no_new_low, index=low_series.index)

    df["no_new_low_days"] = df.groupby("ts_code")["low"].transform(calc_no_new_low)
    log(f"  连续不创新低完成, 耗时{time.time()-t0:.1f}s")

    return df


def filter_dip_candidates(day_df):
    """
    低吸策略筛选:
    1. 跌幅-6%到-3%
    2. 量比<0.8（缩量下跌，抛压衰竭）
    3. 收盘价在MA20附近（MA20*0.95 ~ MA20*1.05）
    4. 偏离MA5超过3%（close < ma5 * 0.97，超卖）
    5. 连续下跌>=2天
    6. 换手率3%-15%
    7. 成交额>=3000万
    8. 股价>3元
    """
    mask = (
        (day_df["pct_chg"] >= DIP_PCT_LO) &
        (day_df["pct_chg"] <= DIP_PCT_HI) &
        (day_df["vol_ratio"] <= DIP_VOL_RATIO_MAX) &
        (day_df["vol_ratio"] > 0) &
        (day_df["ma20"].notna()) &
        (day_df["close"] >= day_df["ma20"] * (1 - DIP_MA20_TOLERANCE)) &
        (day_df["close"] <= day_df["ma20"] * (1 + DIP_MA20_TOLERANCE)) &
        (day_df["ma5"].notna()) &
        (day_df["close"] < day_df["ma5"] * (1 - DIP_MA5_DEVIATION)) &
        (day_df["down_days"] >= 2) &
        (day_df["turnover_rate"] >= DIP_TURN_MIN) &
        (day_df["turnover_rate"] <= DIP_TURN_MAX) &
        (day_df["amount"] >= DIP_MIN_AMOUNT) &
        (day_df["close"] > 3.0)
    )
    candidates = day_df[mask].copy()
    if not candidates.empty:
        candidates["strategy"] = "dip"
        candidates["score"] = (
            (candidates["pct_chg"].abs() / 6.0) * 30 +
            (1 - candidates["vol_ratio"] / DIP_VOL_RATIO_MAX) * 30 +
            ((day_df["ma5"] - candidates["close"]) / candidates["ma5"] * 100) * 20 +
            (candidates["down_days"] / 5.0) * 20
        )
        candidates = candidates.sort_values("score", ascending=False)
        candidates = candidates.head(MAX_PICKS_PER_DAY)
    return candidates


def filter_stabilize_candidates(day_df):
    """
    右侧企稳策略筛选:
    1. 连续2天不创新低（no_new_low_days >= 2）
    2. 今日阳线涨幅>2%
    3. 今日放量（量比>1.5）
    4. 收盘价站上MA5
    5. 换手率3%-15%
    6. 成交额>=3000万
    7. 股价>3元
    """
    mask = (
        (day_df["no_new_low_days"] >= 2) &
        (day_df["pct_chg"] >= STABILIZE_PCT_MIN) &
        (day_df["vol_ratio"] >= STABILIZE_VOL_RATIO_MIN) &
        (day_df["ma5"].notna()) &
        (day_df["close"] > day_df["ma5"]) &
        (day_df["turnover_rate"] >= STABILIZE_TURN_MIN) &
        (day_df["turnover_rate"] <= STABILIZE_TURN_MAX) &
        (day_df["amount"] >= STABILIZE_MIN_AMOUNT) &
        (day_df["close"] > 3.0)
    )
    candidates = day_df[mask].copy()
    if not candidates.empty:
        candidates["strategy"] = "stabilize"
        candidates["score"] = (
            (candidates["pct_chg"] / 10.0) * 30 +
            (candidates["vol_ratio"] / 3.0) * 30 +
            (candidates["no_new_low_days"] / 5.0) * 20 +
            ((candidates["close"] - candidates["ma5"]) / candidates["ma5"] * 100) * 20
        )
        candidates = candidates.sort_values("score", ascending=False)
        candidates = candidates.head(MAX_PICKS_PER_DAY)
    return candidates


def simulate_sell(buy_price, daily_bars, max_hold=3):
    """
    卖出逻辑:
    - T+1日开盘价卖出
    - -2%止损
    - +5%止盈
    - 最大持股3天
    """
    stop_price = buy_price * (1 + STOP_LOSS)
    profit_target = buy_price * 1.05

    for day_idx, bar in enumerate(daily_bars):
        d_open = bar["open"]
        d_high = bar["high"]
        d_low = bar["low"]
        d_close = bar["close"]
        is_last = (day_idx == len(daily_bars) - 1)
        hold_days = day_idx + 1

        if d_open <= 0 or d_high <= 0 or d_low <= 0 or d_close <= 0:
            if is_last:
                pnl_pct = (d_close / buy_price - 1) if d_close > 0 else 0
                return d_close if d_close > 0 else buy_price, f"数据缺失({pnl_pct*100:+.2f}%)", pnl_pct
            continue

        # 止损检查
        if d_open <= stop_price:
            pnl_pct = (d_open / buy_price - 1)
            return d_open, f"第{hold_days}日开盘止损-2%", pnl_pct

        if d_low <= stop_price:
            pnl_pct = (stop_price / buy_price - 1)
            return stop_price, f"第{hold_days}日盘中止损-2%", pnl_pct

        # 止盈检查
        if d_high >= profit_target:
            pnl_pct = (profit_target / buy_price - 1)
            return profit_target, f"第{hold_days}日盘中止盈+5%", pnl_pct

        # 最大持股3天
        if hold_days >= max_hold:
            pnl_pct = (d_close / buy_price - 1)
            return d_close, f"持股{hold_days}天卖出({pnl_pct*100:+.2f}%)", pnl_pct

        if is_last:
            pnl_pct = (d_close / buy_price - 1)
            return d_close, f"持股{hold_days}日收盘({pnl_pct*100:+.2f}%)", pnl_pct

    return buy_price, "无数据", 0.0


def run_backtest(df, strategy_name, filter_func, actual_start_date=None):
    """
    回测核心逻辑
    """
    trading_dates = sorted(df["trade_date"].unique())
    log(f"  总交易日: {len(trading_dates)}")
    if actual_start_date:
        log(f"  实际交易起始: {actual_start_date}")

    capital = INITIAL_CAPITAL
    trade_log = []
    total_trades = 0
    win_trades = 0

    start_idx = MA20_WINDOW + VOL_LOOKBACK + 1
    log(f"  回测起始索引: {start_idx}")

    date_groups = dict(iter(df.groupby("trade_date")))

    all_trades = []
    processed_days = 0

    i = start_idx
    while i < len(trading_dates) - 1:
        t_date = trading_dates[i]
        processed_days += 1
        if processed_days % 50 == 0:
            log(f"  已处理{processed_days}个交易日...")
        
        if actual_start_date and t_date < pd.Timestamp(actual_start_date):
            i += 1
            continue

        if t_date not in date_groups:
            i += 1
            continue

        day_df = date_groups[t_date].copy()
        candidates = filter_func(day_df)
        if candidates.empty:
            i += 1
            continue

        n = len(candidates)
        alloc = capital / n

        buyable = candidates[candidates["close"] * MIN_LOTS <= alloc].copy()
        if buyable.empty:
            i += 1
            continue

        if len(buyable) < n:
            real_alloc = capital / len(buyable)
            buyable = buyable[buyable["close"] * MIN_LOTS <= real_alloc]
            if buyable.empty:
                i += 1
                continue
        else:
            real_alloc = alloc

        day_trades = []
        day_pnl = 0.0

        for _, row in buyable.iterrows():
            code = row["ts_code"]
            buy_price = float(row["close"])
            shares = int(real_alloc / buy_price / MIN_LOTS) * MIN_LOTS
            if shares <= 0:
                continue
            actual_cost = shares * buy_price

            daily_bars = []
            remaining_dates = trading_dates[i+1:i+1+3]
            
            for t_n_date in remaining_dates:
                if t_n_date not in date_groups:
                    continue
                t_n_day = date_groups[t_n_date]
                t_n_lookup = t_n_day.set_index("ts_code")
                if code not in t_n_lookup.index:
                    continue
                t_n_row = t_n_lookup.loc[code]
                if isinstance(t_n_row, pd.DataFrame):
                    t_n_row = t_n_row.iloc[0]
                
                o, h, l, c = float(t_n_row["open"]), float(t_n_row["high"]), float(t_n_row["low"]), float(t_n_row["close"])
                if any(pd.isna([o, h, l, c])) or o <= 0 or h <= 0 or l <= 0 or c <= 0:
                    continue
                    
                daily_bars.append({
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "date": t_n_date,
                })

            if not daily_bars:
                continue

            sell_price, reason, pnl_pct = simulate_sell(buy_price, daily_bars, max_hold=3)

            proceeds = shares * sell_price
            pnl_amt = proceeds - actual_cost
            day_pnl += pnl_amt
            total_trades += 1
            if pnl_pct > 0:
                win_trades += 1

            sell_date = daily_bars[0]["date"]

            trade_record = {
                "buy_date": t_date,
                "sell_date": sell_date,
                "code": code,
                "strategy": row["strategy"],
                "score": row["score"],
                "buy_price": buy_price,
                "sell_price": sell_price,
                "shares": shares,
                "cost": actual_cost,
                "proceeds": proceeds,
                "pnl_amt": pnl_amt,
                "pnl_pct": pnl_pct,
                "reason": reason,
            }
            day_trades.append(trade_record)
            all_trades.append(trade_record)

        if day_trades:
            capital += day_pnl
            trade_log.append({
                "date": t_date,
                "details": day_trades,
                "day_pnl": day_pnl,
                "capital_after": capital,
            })
            log(f"  {pd.Timestamp(t_date).date()} | {len(day_trades)}只 | "
                f"盈亏{day_pnl:+,.2f} | 资金{capital:,.2f}")

        i += 1

    log(f"  回测完成，共处理{processed_days}个交易日")
    return trade_log, all_trades, capital, total_trades, win_trades


def print_report(trade_log, all_trades, final_capital, total_trades, win_trades, strategy_name):
    log(f"\n{'=' * 80}")
    log(f"  回测报告 — [{strategy_name}]")
    log(f"{'=' * 80}")
    log(f"  初始资金:   {INITIAL_CAPITAL:>12,.2f} 元")
    log(f"  最终资金:   {final_capital:>12,.2f} 元")
    log(f"  总盈亏:     {final_capital - INITIAL_CAPITAL:>+12,.2f} 元")
    log(f"  总收益率:   {(final_capital / INITIAL_CAPITAL - 1) * 100:>+11.2f}%")
    log(f"  交易笔数:   {total_trades:>12d}")
    log(f"  盈利笔数:   {win_trades:>12d}")
    log(f"  亏损笔数:   {total_trades - win_trades:>12d}")
    log(f"  胜率:       {win_trades / max(total_trades, 1) * 100:>11.1f}%")

    if total_trades > 0:
        pnls = [t["pnl_pct"] for t in all_trades]
        avg_pnl = np.mean(pnls) * 100
        max_win = max(pnls) * 100
        max_loss = min(pnls) * 100
        log(f"  平均每笔:   {avg_pnl:>+11.2f}%")
        log(f"  最大单笔盈利: {max_win:>+8.2f}%")
        log(f"  最大单笔亏损: {max_loss:>+8.2f}%")

        max_consecutive_loss = 0
        current_consecutive_loss = 0
        for pnl in pnls:
            if pnl < 0:
                current_consecutive_loss += 1
                max_consecutive_loss = max(max_consecutive_loss, current_consecutive_loss)
            else:
                current_consecutive_loss = 0
        log(f"  最大连续亏损: {max_consecutive_loss:>8d}笔")

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        avg_win = np.mean(wins) * 100 if wins else 0
        avg_loss = abs(np.mean(losses)) * 100 if losses else 0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
        log(f"  平均盈利/平均亏损: {profit_loss_ratio:>10.2f}")

    log(f"\n{'─' * 80}")
    log(f"  逐笔交易明细:")
    log(f"    {'序号':>4} {'买入日':<12} {'卖出日':<12} {'代码':<12} {'策略':<10} "
        f"{'买价':>8} {'卖价':>8} {'股数':>8} {'成本':>12} {'盈亏%':>8} {'盈亏元':>12} {'原因':<20}")
    log(f"  {'─' * 4} {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 10} "
        f"{'─' * 8} {'─' * 8} {'─' * 8} {'─' * 12} {'─' * 8} {'─' * 12} {'─' * 20}")

    for idx, t in enumerate(all_trades, 1):
        log(f"    {idx:>4} {pd.Timestamp(t['buy_date']).date():<12} "
            f"{pd.Timestamp(t['sell_date']).date():<12} {t['code']:<12} {t['strategy']:<10} "
            f"{t['buy_price']:>8.2f} {t['sell_price']:>8.2f} {t['shares']:>8} "
            f"{t['cost']:>12,.2f} {t['pnl_pct']*100:>+7.2f}% {t['pnl_amt']:>+12,.2f} {t['reason']:<20}")

    log(f"\n{'─' * 80}")
    log(f"  逐日汇总:")
    log(f"  {'买入日':<12} {'交易数':>6} {'当日盈亏':>14} {'资金余额':>14} {'累计收益率':>12}")
    log(f"  {'─' * 12} {'─' * 6} {'─' * 14} {'─' * 14} {'─' * 12}")

    for entry in trade_log:
        cum_return = (entry["capital_after"] / INITIAL_CAPITAL - 1) * 100
        log(f"  {pd.Timestamp(entry['date']).date():<12} {len(entry['details']):>6} "
            f"{entry['day_pnl']:>+14,.2f} {entry['capital_after']:>14,.2f} {cum_return:>+11.2f}%")

    log(f"\n{'=' * 80}")


def main():
    global _out
    try:
        _out = open(OUTPUT_FILE, "w", encoding="utf-8")

        log("=" * 80)
        log("  双策略回测 — 低吸策略 + 右侧企稳策略")
        log("  回测区间: 2025-05-01 ~ 2026-05-25")
        log("  初始资金: 100万元 | 止损-2% | 最大持股3天")
        log("=" * 80)

        df = load_data_cached("2025-05-01", "2025-08-31")
        log("  开始计算指标...")
        df = compute_indicators(df)
        log("  指标计算完成，开始回测...")

        # 策略1: 低吸策略
        log(f"\n{'#' * 80}")
        log("  策略1: 低吸策略")
        log(f"{'#' * 80}")
        trade_log1, all_trades1, final_capital1, total_trades1, win_trades1 = run_backtest(
            df, "dip", filter_dip_candidates, actual_start_date="2025-05-01"
        )
        print_report(trade_log1, all_trades1, final_capital1, total_trades1, win_trades1, "低吸策略")

        # 策略2: 右侧企稳策略
        log(f"\n{'#' * 80}")
        log("  策略2: 右侧企稳策略")
        log(f"{'#' * 80}")
        trade_log2, all_trades2, final_capital2, total_trades2, win_trades2 = run_backtest(
            df, "stabilize", filter_stabilize_candidates, actual_start_date="2025-05-01"
        )
        print_report(trade_log2, all_trades2, final_capital2, total_trades2, win_trades2, "右侧企稳策略")

        # 对比总结
        log(f"\n{'=' * 80}")
        log("  策略对比总结")
        log(f"{'=' * 80}")
        log(f"  {'指标':<20} {'低吸策略':>15} {'右侧企稳策略':>15}")
        log(f"  {'─' * 20} {'─' * 15} {'─' * 15}")
        log(f"  {'最终资金':<20} {final_capital1:>15,.2f} {final_capital2:>15,.2f}")
        log(f"  {'总收益率':<20} {(final_capital1/INITIAL_CAPITAL-1)*100:>+14.2f}% {(final_capital2/INITIAL_CAPITAL-1)*100:>+14.2f}%")
        log(f"  {'交易笔数':<20} {total_trades1:>15d} {total_trades2:>15d}")
        log(f"  {'胜率':<20} {win_trades1/max(total_trades1,1)*100:>+14.1f}% {win_trades2/max(total_trades2,1)*100:>+14.1f}%")

        log(f"\n  结果已保存到: {OUTPUT_FILE}")
        _out.close()
    except Exception as e:
        log(f"\n错误: {e}")
        import traceback
        log(traceback.format_exc())
        if _out:
            _out.close()


if __name__ == "__main__":
    main()
