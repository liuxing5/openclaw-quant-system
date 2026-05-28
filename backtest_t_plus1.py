"""
八步法回测 — T日14:40买入, T+1早盘9:30-10:30卖出
================================================
回测区间: 2025-05-01 ~ 2026-05-25
买入逻辑: T日14:40以收盘价买入（模拟尾盘买入）
卖出逻辑: T+1日早盘9:30-10:30以开盘价卖出
最大持股: 不超过3天
止损: -2%
"""
import sys
import psycopg2
import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor
import time
from datetime import date

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"

INITIAL_CAPITAL = 1_000_000
STOP_LOSS = -0.02  # -2%止损
MIN_LOTS = 100     # 最少100股

# 八步法筛选参数
STABLE_PCT_LO = 3.0
STABLE_PCT_HI = 6.0
UPPER_PCT_LO = 6.0
UPPER_PCT_HI = 9.7
STABLE_MIN_AMOUNT = 50_000_000
STABLE_MAX_AMOUNT = 5_000_000_000
UPPER_MIN_AMOUNT = 30_000_000
UPPER_MAX_AMOUNT = 3_000_000_000
TURN_MIN = 5.0
TURN_MAX = 10.0
VOL_RATIO_MIN = 1.5
VOL_RATIO_MAX = 8.0
MA_WINDOW = 5
VOL_LOOKBACK = 10
MAX_PICKS_PER_DAY = 5

OUTPUT_FILE = r'd:\pythonProject\openclaw-quant-system\diag_key_stocks_out.txt'

_out = None

def log(msg="", end="\n"):
    global _out
    print(msg, end=end, flush=True)
    if _out:
        _out.write(msg + end)
        _out.flush()


def load_data(start_date, end_date, lookback_days=30):
    # 计算前置日期，用于计算MA5和量比等指标
    from datetime import datetime, timedelta
    sd = datetime.strptime(start_date, "%Y-%m-%d")
    lookback_start = (sd - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    
    log(f"步骤1: 从数据库加载数据 {start_date} ~ {end_date} (前置数据从{lookback_start}开始)...")
    t0 = time.time()

    max_retries = 5
    for attempt in range(max_retries):
        conn = None
        try:
            conn = psycopg2.connect(DB_URL, connect_timeout=60)
            conn.autocommit = True
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # 设置statement_timeout防止查询超时
            cur.execute("SET statement_timeout = '300s'")

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
            return df

        except psycopg2.OperationalError as e:
            if conn:
                try:
                    conn.close()
                except:
                    pass
            if attempt < max_retries - 1:
                log(f"  连接失败(尝试{attempt+1}/{max_retries}): {e}，5秒后重试...")
                time.sleep(5)
            else:
                raise


def compute_indicators(df):
    log("步骤2: 计算MA5...")
    t0 = time.time()
    df["ma5"] = df.groupby("ts_code")["close"].transform(
        lambda x: x.rolling(MA_WINDOW, min_periods=MA_WINDOW).mean().shift(1)
    )
    log(f"  MA5完成, 耗时{time.time()-t0:.1f}s")

    log("步骤3: 计算量比...")
    t0 = time.time()
    avg_vol = df.groupby("ts_code")["volume"].transform(
        lambda x: x.rolling(VOL_LOOKBACK, min_periods=5).mean().shift(1)
    )
    df["vol_ratio"] = df["volume"] / avg_vol
    log(f"  量比完成, 耗时{time.time()-t0:.1f}s")

    log("步骤4: 计算连板...")
    t0 = time.time()

    def calc_streak(pct_series):
        streaks = [0] * len(pct_series)
        for i in range(len(pct_series)):
            if pct_series.iloc[i] >= 9.5:
                streaks[i] = streaks[i - 1] + 1 if i > 0 else 1
            else:
                streaks[i] = 0
        return pd.Series(streaks, index=pct_series.index)

    df["streak"] = df.groupby("ts_code")["pct_chg"].transform(calc_streak)
    log(f"  连板完成, 耗时{time.time()-t0:.1f}s")
    return df


def filter_candidates(day_df, mode="both"):
    candidates_list = []

    if mode in ("stable", "both"):
        mask_s = (
            (day_df["pct_chg"] >= STABLE_PCT_LO) &
            (day_df["pct_chg"] <= STABLE_PCT_HI) &
            (day_df["amount"] >= STABLE_MIN_AMOUNT) &
            (day_df["amount"] <= STABLE_MAX_AMOUNT) &
            (day_df["turnover_rate"] >= TURN_MIN) &
            (day_df["turnover_rate"] <= TURN_MAX) &
            (day_df["vol_ratio"] >= VOL_RATIO_MIN) &
            (day_df["vol_ratio"] <= VOL_RATIO_MAX) &
            (day_df["ma5"].notna()) &
            (day_df["close"] > day_df["ma5"]) &
            (day_df["close"] > 3.0)
        )
        stable_cands = day_df[mask_s].copy()
        if not stable_cands.empty:
            stable_cands["path"] = "stable"
            candidates_list.append(stable_cands)

    if mode in ("upper", "both"):
        mask_u = (
            (day_df["pct_chg"] >= UPPER_PCT_LO) &
            (day_df["pct_chg"] <= UPPER_PCT_HI) &
            (day_df["amount"] >= UPPER_MIN_AMOUNT) &
            (day_df["amount"] <= UPPER_MAX_AMOUNT) &
            (day_df["turnover_rate"] >= TURN_MIN) &
            (day_df["turnover_rate"] <= TURN_MAX) &
            (day_df["vol_ratio"] >= VOL_RATIO_MIN) &
            (day_df["vol_ratio"] <= VOL_RATIO_MAX) &
            (day_df["ma5"].notna()) &
            (day_df["close"] > day_df["ma5"]) &
            (day_df["close"] > 3.0)
        )
        upper_cands = day_df[mask_u].copy()
        if not upper_cands.empty:
            upper_cands["path"] = "upper"
            candidates_list.append(upper_cands)

    if not candidates_list:
        return pd.DataFrame()

    candidates = pd.concat(candidates_list, ignore_index=True)
    candidates["score"] = candidates.apply(lambda r: score_stock(r, r["path"]), axis=1)
    candidates = candidates.sort_values("score", ascending=False)
    candidates = candidates.drop_duplicates(subset=["ts_code"], keep="first")
    candidates = candidates.head(MAX_PICKS_PER_DAY)
    return candidates


def score_stock(row, path):
    score = 0
    pct = row["pct_chg"]
    vr = row["vol_ratio"] if not np.isnan(row.get("vol_ratio", np.nan)) else 0
    turn = row["turnover_rate"] if not np.isnan(row.get("turnover_rate", np.nan)) else 0
    streak = row["streak"]

    if path == "stable" and STABLE_PCT_LO <= pct <= STABLE_PCT_HI:
        score += 10
    elif path == "upper" and UPPER_PCT_LO <= pct <= UPPER_PCT_HI:
        score += 15

    if 1.5 <= vr <= 3.0:
        score += 15
    elif 3.0 < vr <= 5.0:
        score += 10
    elif vr > 5.0:
        score += 5

    if 5.0 <= turn <= 8.0:
        score += 15
    elif 8.0 < turn <= 10.0:
        score += 8

    if streak == 0:
        score += 5
    elif streak == 1:
        score += 20
    elif streak == 2:
        score += 30
    elif streak >= 3:
        score += 15

    return score


def simulate_sell(buy_price, daily_bars, max_hold=None):
    """
    卖出逻辑（平衡胜率+盈亏比优化版）:
    - T+1日：盈利>=3%立即止盈，<3%继续持有让利润奔跑
    - T+1日：亏损但未到-2%，继续持有
    - T+2及以后：-2%严格止损，+5%止盈
    - 最大持股3天，超时强制卖出（控制风险）
    """
    stop_price = buy_price * (1 + STOP_LOSS)  # -2%止损价
    profit_target_t1 = buy_price * 1.03  # T+1日3%止盈
    profit_target = buy_price * 1.05  # T+2及以后5%止盈

    for day_idx, bar in enumerate(daily_bars):
        d_open = bar["open"]
        d_high = bar["high"]
        d_low = bar["low"]
        d_close = bar["close"]
        is_last = (day_idx == len(daily_bars) - 1)
        hold_days = day_idx + 1  # 已持股天数

        if d_open <= 0 or d_high <= 0 or d_low <= 0 or d_close <= 0:
            if is_last:
                pnl_pct = (d_close / buy_price - 1) if d_close > 0 else 0
                return d_close if d_close > 0 else buy_price, f"数据缺失({pnl_pct*100:+.2f}%)", pnl_pct
            continue

        # T+1日（第1天）策略
        if day_idx == 0:
            # 开盘触发止损，立即止损
            if d_open <= stop_price:
                pnl_pct = (d_open / buy_price - 1)
                return d_open, f"T+1开盘止损-2%", pnl_pct
            # 开盘盈利>=3%，立即止盈
            if d_open >= profit_target_t1:
                pnl_pct = (d_open / buy_price - 1)
                return d_open, f"T+1开盘止盈+3%", pnl_pct
            # 开盘盈利<3%或微亏，继续持有让利润奔跑
            continue

        # T+2及以后策略
        # 严格止损检查
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

        # 最大持股3天，超时强制卖出
        if hold_days >= 3:
            pnl_pct = (d_close / buy_price - 1)
            return d_close, f"持股{hold_days}天卖出({pnl_pct*100:+.2f}%)", pnl_pct

        # 最后一天强制卖出
        if is_last:
            pnl_pct = (d_close / buy_price - 1)
            return d_close, f"持股{hold_days}日收盘({pnl_pct*100:+.2f}%)", pnl_pct

    return buy_price, "无数据", 0.0


def run_backtest(df, mode="both", max_hold=None, actual_start_date=None):
    """
    回测核心逻辑:
    - T日14:40以收盘价买入
    - T+1日早盘9:30-10:30以开盘价卖出
    - max_hold=None表示持股不限天数
    - -2%止损
    - actual_start_date: 实际交易开始日期（之前的数据只用于计算指标）
    """
    trading_dates = sorted(df["trade_date"].unique())
    date_idx_map = {d: i for i, d in enumerate(trading_dates)}
    hold_label = f"{max_hold}天" if max_hold else "不限"
    log(f"  总交易日: {len(trading_dates)}, 最大持股: {hold_label}")
    if actual_start_date:
        log(f"  实际交易起始: {actual_start_date}")

    capital = INITIAL_CAPITAL
    trade_log = []
    total_trades = 0
    win_trades = 0

    start_idx = VOL_LOOKBACK + MA_WINDOW + 1

    date_groups = dict(iter(df.groupby("trade_date")))

    all_trades = []  # 逐笔交易记录

    i = start_idx
    while i < len(trading_dates) - 1:  # 至少留1天卖出
        t_date = trading_dates[i]
        
        # 如果设置了实际交易起始日期，跳过之前的日期
        if actual_start_date and t_date < pd.Timestamp(actual_start_date):
            i += 1
            continue

        if t_date not in date_groups:
            i += 1
            continue

        day_df = date_groups[t_date].copy()
        candidates = filter_candidates(day_df, mode=mode)
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

            # 获取后续K线用于卖出模拟
            daily_bars = []
            if max_hold is None:
                # 持股不限天数，获取所有剩余交易日
                remaining_dates = trading_dates[i+1:]
            else:
                # 限制持股天数
                remaining_dates = trading_dates[i+1:i+1+max_hold]
            
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
                
                # 验证数据有效性
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

            sell_price, reason, pnl_pct = simulate_sell(buy_price, daily_bars, max_hold)

            proceeds = shares * sell_price
            pnl_amt = proceeds - actual_cost
            day_pnl += pnl_amt
            total_trades += 1
            if pnl_pct > 0:
                win_trades += 1

            sell_date = daily_bars[0]["date"]  # T+1开盘卖出

            trade_record = {
                "buy_date": t_date,
                "sell_date": sell_date,
                "code": code,
                "path": row["path"],
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

    return trade_log, all_trades, capital, total_trades, win_trades


def print_report(trade_log, all_trades, final_capital, total_trades, win_trades, mode_label, max_hold):
    log(f"\n{'=' * 80}")
    log(f"  八步法回测报告 — [{mode_label}] 最大持股{max_hold}天")
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

        # 连续亏损统计
        max_consecutive_loss = 0
        current_consecutive_loss = 0
        for pnl in pnls:
            if pnl < 0:
                current_consecutive_loss += 1
                max_consecutive_loss = max(max_consecutive_loss, current_consecutive_loss)
            else:
                current_consecutive_loss = 0
        log(f"  最大连续亏损: {max_consecutive_loss:>8d}笔")

        # 盈亏比
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        avg_win = np.mean(wins) * 100 if wins else 0
        avg_loss = abs(np.mean(losses)) * 100 if losses else 0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
        log(f"  平均盈利/平均亏损: {profit_loss_ratio:>10.2f}")
    log(f"{'─' * 80}")

    # 逐笔交易明细
    log(f"\n  逐笔交易明细:")
    log(f"  {'序号':>4} {'买入日':<12} {'卖出日':<12} {'代码':<12} {'类型':>6} "
        f"{'买价':>8} {'卖价':>8} {'股数':>8} {'成本':>10} {'盈亏%':>8} {'盈亏元':>10} {'原因':<20}")
    log(f"  {'─'*4} {'─'*12} {'─'*12} {'─'*12} {'─'*6} "
        f"{'─'*8} {'─'*8} {'─'*8} {'─'*10} {'─'*8} {'─'*10} {'─'*20}")

    for idx, t in enumerate(all_trades, 1):
        buy_str = pd.Timestamp(t['buy_date']).date()
        sell_str = pd.Timestamp(t['sell_date']).date()
        log(f"  {idx:>4} {buy_str!s:<12} {sell_str!s:<12} {t['code']:<12} {t['path']:>6} "
            f"{t['buy_price']:>8.2f} {t['sell_price']:>8.2f} {t['shares']:>8} {t['cost']:>10.2f} "
            f"{t['pnl_pct']*100:>+7.2f}% {t['pnl_amt']:>+9.2f} {t['reason']:<20}")

    # 按日汇总
    log(f"\n  逐日汇总:")
    log(f"  {'买入日':<12} {'交易数':>6} {'当日盈亏':>12} {'资金余额':>14} {'累计收益率':>10}")
    log(f"  {'─'*12} {'─'*6} {'─'*12} {'─'*14} {'─'*10}")

    running = INITIAL_CAPITAL
    for t in trade_log:
        running += t["day_pnl"]
        cum_ret = (running / INITIAL_CAPITAL - 1) * 100
        n_stocks = len(t["details"])
        t_str = pd.Timestamp(t['date']).date()
        log(f"  {t_str!s:<12} "
            f"{n_stocks:>6} {t['day_pnl']:>+12,.2f} {running:>14,.2f} {cum_ret:>+9.2f}%")

    log(f"\n{'=' * 80}")
    return final_capital


def main():
    global _out
    try:
        _out = open(OUTPUT_FILE, "w", encoding="utf-8")
    except Exception as e:
        print(f"Error opening output file: {e}")
        import traceback
        traceback.print_exc()
        return

    log("=" * 80)
    log("  八步法回测 — T日14:40买入, T+1早盘9:30-10:30卖出")
    log("  回测区间: 2025-05-01 ~ 2026-05-25")
    log("  初始资金: 100万元 | 止损-2% | 持股不限天数")
    log("=" * 80)

    start_date = "2025-05-01"
    end_date = "2026-05-25"
    actual_start = "2025-05-01"  # 实际交易开始日期

    try:
        df = load_data(start_date, end_date, lookback_days=30)
        df = compute_indicators(df)

        mode = "both"  # 稳健+高位都买
        max_hold = None  # 持股不限天数

        log(f"\n{'#' * 80}")
        log(f"  回测: 稳健+高位都买 · 持股不限天数")
        log(f"{'#' * 80}")

        trade_log, all_trades, final_capital, total_trades, win_trades = run_backtest(
            df, mode=mode, max_hold=max_hold, actual_start_date=actual_start
        )

        print_report(trade_log, all_trades, final_capital, total_trades, win_trades,
                     f"稳健+高位·持股不限", max_hold if max_hold else "不限")
    except Exception as e:
        log(f"\nERROR: {e}")
        import traceback
        log(traceback.format_exc())
    finally:
        if _out:
            _out.close()
        print(f"\n结果已保存到: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
