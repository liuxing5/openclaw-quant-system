import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, line_buffering=True)

import psycopg2
import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor
import time

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"

INITIAL_CAPITAL = 100_000
PROFIT_TARGET = 0.02
STOP_LOSS = 0.02
MIN_LOTS = 100

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

OUTPUT_FILE = r"d:\pythonProject\openclaw-quant-system\strategies\overnight_8step\backtest_result.txt"

_out = None

def log(msg=""):
    global _out
    print(msg, flush=True)
    if _out:
        _out.write(msg + "\n")
        _out.flush()


def load_data():
    log("步骤1: 从数据库加载数据(服务端游标分批)...")
    t0 = time.time()
    conn = psycopg2.connect(DB_URL, connect_timeout=60)

    cur_name = "backtest_cursor"
    cur = conn.cursor(cur_name, cursor_factory=RealDictCursor, withhold=True)

    cur.execute("""
        SELECT ts_code, trade_date, open, high, low, close,
               volume, amount, pct_chg, turnover_rate
        FROM daily_quotes
        WHERE trade_date >= '2025-01-01'
          AND pct_chg IS NOT NULL
          AND amount IS NOT NULL
          AND turnover_rate IS NOT NULL
        ORDER BY ts_code, trade_date
    """)

    chunks = []
    batch_size = 20000
    total = 0
    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break
        chunks.append(pd.DataFrame(rows))
        total += len(rows)
        if total % 100000 < batch_size:
            log(f"  已加载 {total} 条...")

    cur.close()
    conn.close()

    df = pd.concat(chunks, ignore_index=True)
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["close", "pct_chg", "amount", "turnover_rate"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    log(f"  加载完成: {len(df)} 条, {df['ts_code'].nunique()} 只, 耗时{time.time()-t0:.1f}s")
    log(f"  日期: {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")
    return df


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

    log(f"  量比覆盖: {df['vol_ratio'].notna().sum()}/{len(df)} ({df['vol_ratio'].notna().mean()*100:.1f}%)")
    log(f"  MA5覆盖: {df['ma5'].notna().sum()}/{len(df)} ({df['ma5'].notna().mean()*100:.1f}%)")
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


def simulate_sell(buy_price, t1_open, t1_high, t1_low, t1_close):
    target = buy_price * (1 + PROFIT_TARGET)
    stop = buy_price * (1 - STOP_LOSS)

    if t1_open >= target:
        return t1_open, "开盘止盈+2%", (t1_open / buy_price - 1)
    if t1_open <= stop:
        return t1_open, "开盘止损-2%", (t1_open / buy_price - 1)

    hit_profit = t1_high >= target
    hit_stop = t1_low <= stop

    if hit_profit and hit_stop:
        if t1_open >= buy_price:
            return target, "盘中止盈+2%", (target / buy_price - 1)
        else:
            return stop, "盘中止损-2%", (stop / buy_price - 1)
    elif hit_profit:
        return target, "盘中止盈+2%", (target / buy_price - 1)
    elif hit_stop:
        return stop, "盘中止损-2%", (stop / buy_price - 1)

    pnl_pct = (t1_close / buy_price - 1)
    return t1_close, f"10:30卖出({pnl_pct*100:+.2f}%)", pnl_pct


def run_backtest(df, mode="both"):
    trading_dates = sorted(df["trade_date"].unique())
    log(f"  总交易日: {len(trading_dates)}")

    capital = INITIAL_CAPITAL
    trade_log = []
    total_trades = 0
    win_trades = 0

    start_idx = VOL_LOOKBACK + MA_WINDOW + 1

    date_groups = dict(iter(df.groupby("trade_date")))

    for i in range(start_idx, len(trading_dates) - 1):
        t_date = trading_dates[i]
        t1_date = trading_dates[i + 1]

        if t_date not in date_groups or t1_date not in date_groups:
            continue

        day_df = date_groups[t_date].copy()
        candidates = filter_candidates(day_df, mode=mode)
        if candidates.empty:
            continue

        n = len(candidates)
        alloc = capital / n
        day_pnl = 0.0
        day_details = []

        buyable = candidates[candidates["close"] * MIN_LOTS <= alloc].copy()
        if buyable.empty:
            continue

        if len(buyable) < n:
            real_alloc = capital / len(buyable)
            buyable = buyable[buyable["close"] * MIN_LOTS <= real_alloc]
            if buyable.empty:
                continue
        else:
            real_alloc = alloc

        t1_day = date_groups[t1_date]
        t1_lookup = t1_day.set_index("ts_code")

        for _, row in buyable.iterrows():
            code = row["ts_code"]
            buy_price = float(row["close"])
            shares = int(real_alloc / buy_price / MIN_LOTS) * MIN_LOTS
            if shares <= 0:
                continue
            actual_cost = shares * buy_price

            if code not in t1_lookup.index:
                continue

            t1_row = t1_lookup.loc[code]
            if isinstance(t1_row, pd.DataFrame):
                t1_row = t1_row.iloc[0]

            t1_open = float(t1_row["open"])
            t1_high = float(t1_row["high"])
            t1_low = float(t1_row["low"])
            t1_close = float(t1_row["close"])

            if t1_open <= 0 or t1_high <= 0 or t1_low <= 0 or t1_close <= 0:
                continue

            sell_price, reason, pnl_pct = simulate_sell(
                buy_price, t1_open, t1_high, t1_low, t1_close
            )
            proceeds = shares * sell_price
            pnl_amt = proceeds - actual_cost
            day_pnl += pnl_amt
            total_trades += 1
            if pnl_pct > 0:
                win_trades += 1

            day_details.append({
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
            })

        if day_details:
            capital += day_pnl
            trade_log.append({
                "date": t_date,
                "t1_date": t1_date,
                "details": day_details,
                "day_pnl": day_pnl,
                "capital_after": capital,
            })
            log(f"  {pd.Timestamp(t_date).date()}→{pd.Timestamp(t1_date).date()} | {len(day_details)}只 | "
                f"盈亏{day_pnl:+,.2f} | 资金{capital:,.2f}")

    return trade_log, capital, total_trades, win_trades


def print_report(trade_log, final_capital, total_trades, win_trades, mode_label):
    log(f"\n{'=' * 80}")
    log(f"  隔夜八步法回测报告 — [{mode_label}]")
    log(f"{'=' * 80}")
    log(f"  初始资金:   {INITIAL_CAPITAL:>12,.2f} 元")
    log(f"  最终资金:   {final_capital:>12,.2f} 元")
    log(f"  总盈亏:     {final_capital - INITIAL_CAPITAL:>+12,.2f} 元")
    log(f"  总收益率:   {(final_capital / INITIAL_CAPITAL - 1) * 100:>+11.2f}%")
    log(f"  交易笔数:   {total_trades:>12d}")
    log(f"  盈利笔数:   {win_trades:>12d}")
    log(f"  胜率:       {win_trades / max(total_trades, 1) * 100:>11.1f}%")

    if total_trades > 0:
        pnls = []
        for t in trade_log:
            for d in t["details"]:
                pnls.append(d["pnl_pct"])
        avg_pnl = np.mean(pnls) * 100
        max_win = max(pnls) * 100
        max_loss = min(pnls) * 100
        log(f"  平均每笔:   {avg_pnl:>+11.2f}%")
        log(f"  最大单笔盈利: {max_win:>+8.2f}%")
        log(f"  最大单笔亏损: {max_loss:>+8.2f}%")
    log(f"{'─' * 80}")

    if not trade_log:
        return final_capital

    log(f"\n  逐日明细:")
    log(f"  {'T日':<12} {'T+1日':<12} {'股票数':>6} {'当日盈亏':>12} {'资金余额':>14} {'累计收益率':>10}")
    log(f"  {'─'*12} {'─'*12} {'─'*6} {'─'*12} {'─'*14} {'─'*10}")

    running = INITIAL_CAPITAL
    for t in trade_log:
        running += t["day_pnl"]
        cum_ret = (running / INITIAL_CAPITAL - 1) * 100
        n_stocks = len(t["details"])
        t_str = pd.Timestamp(t['date']).date()
        t1_str = pd.Timestamp(t['t1_date']).date()
        log(f"  {t_str!s:<12} {t1_str!s:<12} "
            f"{n_stocks:>6} {t['day_pnl']:>+12,.2f} {running:>14,.2f} {cum_ret:>+9.2f}%")

    log(f"\n  逐笔明细:")
    for t in trade_log:
        t_str = pd.Timestamp(t['date']).date()
        t1_str = pd.Timestamp(t['t1_date']).date()
        log(f"\n  {t_str} -> T+1: {t1_str}")
        for d in t["details"]:
            log(f"    {d['code']} [{d['path']}] score={d['score']:.0f} | "
                f"买{d['buy_price']:.2f}->卖{d['sell_price']:.2f} | "
                f"{d['shares']}股 | {d['pnl_amt']:+,.2f}元({d['pnl_pct']*100:+.2f}%) | {d['reason']}")

    log(f"\n{'=' * 80}")
    return final_capital


def main():
    global _out
    _out = open(OUTPUT_FILE, "w", encoding="utf-8")

    log("=" * 60)
    log("  隔夜八步法策略回测")
    log("  初始资金: 10万元 | 止盈+2% | 止损-2%")
    log("=" * 60)

    df = load_data()
    df = compute_indicators(df)

    modes = [
        ("stable", "只买稳健池(涨幅3%-6%)"),
        ("upper", "只买高位池(涨幅6%-9.7%)"),
        ("both", "稳健+高位都买"),
    ]

    results = {}
    for mode_key, mode_label in modes:
        log(f"\n{'#' * 80}")
        log(f"  回测模式: {mode_label}")
        log(f"{'#' * 80}")
        trade_log, final_capital, total_trades, win_trades = run_backtest(df, mode=mode_key)
        print_report(trade_log, final_capital, total_trades, win_trades, mode_label)
        results[mode_key] = {
            "label": mode_label,
            "final_capital": final_capital,
            "total_trades": total_trades,
            "win_trades": win_trades,
            "win_rate": win_trades / max(total_trades, 1) * 100,
            "total_return": (final_capital / INITIAL_CAPITAL - 1) * 100,
        }

    log(f"\n{'=' * 80}")
    log(f"  三种模式对比汇总")
    log(f"{'=' * 80}")
    log(f"  {'模式':<28} {'最终资金':>14} {'总收益率':>10} {'交易笔数':>8} {'胜率':>8}")
    log(f"  {'─'*28} {'─'*14} {'─'*10} {'─'*8} {'─'*8}")
    for mode_key, r in results.items():
        log(f"  {r['label']:<28} {r['final_capital']:>14,.2f} "
            f"{r['total_return']:>+9.2f}% {r['total_trades']:>8} {r['win_rate']:>7.1f}%")
    log(f"{'=' * 80}")

    _out.close()
    log(f"\n结果已保存到: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
