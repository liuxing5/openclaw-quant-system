import psycopg2
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import sys
import os

DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"
CACHE_FILE = "data_cache.pkl"

START_DATE = "2025-05-01"
END_DATE = "2026-05-25"
PRELOAD_DAYS = 30

INITIAL_CAPITAL = 1_000_000
MIN_LOTS = 100
MAX_HOLD = 3
STOP_LOSS = -0.02
TAKE_PROFIT_T1 = 0.03
TAKE_PROFIT = 0.05

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# 检查缓存
if os.path.exists(CACHE_FILE):
    log(f"使用缓存数据: {CACHE_FILE}")
    df = pd.read_pickle(CACHE_FILE)
    log(f"缓存加载完成: {len(df)} 条")
else:
    log("连接数据库...")
    conn = None
    for attempt in range(20):
        try:
            conn = psycopg2.connect(DB_URL, sslmode='require', connect_timeout=10)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SET statement_timeout = '300s'")
            log("连接成功!")
            break
        except Exception as e:
            log(f"连接失败 ({attempt+1}/20): {str(e)[:80]}")
            conn = None
            time.sleep(15)
    
    if conn is None:
        log("数据库连接失败")
        sys.exit(1)
    
    try:
        start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
        preload_start = (start_dt - timedelta(days=PRELOAD_DAYS)).strftime("%Y-%m-%d")
        
        log(f"查询数据: {preload_start} ~ {END_DATE}...")
        cur.execute("""
            SELECT ts_code, trade_date, open, high, low, close, volume, amount, turnover_rate, pct_chg
            FROM daily_quotes
            WHERE trade_date >= %s AND trade_date <= %s
            ORDER BY ts_code, trade_date
        """, (preload_start, END_DATE))
        
        rows = cur.fetchall()
        log(f"获取 {len(rows)} 条数据")
        
        cur.close()
        conn.close()
        log("连接已关闭")
        
        df = pd.DataFrame(rows, columns=["ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover_rate", "pct_chg"])
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        for col in ["volume", "amount", "turnover_rate", "pct_chg", "open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        
        # 保存缓存
        df.to_pickle(CACHE_FILE)
        log(f"数据已缓存到: {CACHE_FILE}")
    except Exception as e:
        log(f"数据库错误: {e}")
        sys.exit(1)

log(f"数据加载完成: {len(df)} 条, {df['ts_code'].nunique()} 只股票")

# 计算指标
log("计算MA5/MA20...")
df = df.sort_values(["ts_code", "trade_date"])
df["ma5"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(5).mean())
df["ma20"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(20).mean())

log("计算量比...")
df["vol_5"] = df.groupby("ts_code")["volume"].transform(lambda x: x.rolling(5).mean())
df["vol_ratio"] = df["volume"] / df["vol_5"].replace(0, np.nan)

log("计算连续下跌天数...")
def calc_down_days(group):
    result = []
    count = 0
    for pct in group:
        if pct < 0:
            count += 1
        else:
            count = 0
        result.append(count)
    return result

df["down_days"] = df.groupby("ts_code")["pct_chg"].transform(calc_down_days)

log("计算不创新低天数...")
def calc_no_new_low(group):
    result = []
    min_low = float('inf')
    count = 0
    for low in group:
        if low >= min_low:
            count += 1
        else:
            count = 0
            min_low = low
        result.append(count)
    return result

df["no_new_low_days"] = df.groupby("ts_code")["low"].transform(calc_no_new_low)

# 过滤回测区间
trading_dates = sorted(df[df["trade_date"] >= START_DATE]["trade_date"].unique())
df = df[df["trade_date"] >= START_DATE].copy()
log(f"回测区间: {START_DATE} ~ {END_DATE}, {len(trading_dates)} 个交易日")

# 策略筛选
def filter_dip(day_df):
    mask = (
        (day_df["pct_chg"] >= -6) &
        (day_df["pct_chg"] <= -3) &
        (day_df["vol_ratio"] <= 0.8) &
        (day_df["vol_ratio"] > 0) &
        (day_df["ma20"].notna()) &
        (day_df["close"] >= day_df["ma20"] * 0.95) &
        (day_df["close"] <= day_df["ma20"] * 1.05) &
        (day_df["ma5"].notna()) &
        (day_df["close"] < day_df["ma5"] * 0.97) &
        (day_df["down_days"] >= 2) &
        (day_df["turnover_rate"] >= 3) &
        (day_df["turnover_rate"] <= 15) &
        (day_df["amount"] >= 30000000) &
        (day_df["close"] > 3.0)
    )
    candidates = day_df[mask].copy()
    if len(candidates) == 0:
        return candidates
    candidates["score"] = (
        (candidates["pct_chg"] + 6) / 3 * 30 +
        (1 - candidates["vol_ratio"] / 0.8) * 30 +
        (candidates["ma5"] - candidates["close"]) / candidates["ma5"] * 100 * 2 +
        candidates["down_days"] * 5
    )
    candidates["strategy"] = "低吸"
    return candidates.sort_values("score", ascending=False).head(2)

def filter_stabilize(day_df):
    mask = (
        (day_df["no_new_low_days"] >= 3) &
        (day_df["pct_chg"] >= 3.0) &
        (day_df["vol_ratio"] >= 2.0) &
        (day_df["ma5"].notna()) &
        (day_df["close"] > day_df["ma5"] * 1.02) &
        (day_df["turnover_rate"] >= 3) &
        (day_df["turnover_rate"] <= 15) &
        (day_df["amount"] >= 50000000) &
        (day_df["close"] > 5.0)
    )
    candidates = day_df[mask].copy()
    if len(candidates) == 0:
        return candidates
    candidates["score"] = (
        (candidates["pct_chg"] - 3.0) * 10 +
        (candidates["vol_ratio"] - 2.0) * 10 +
        candidates["no_new_low_days"] * 5
    )
    candidates["strategy"] = "右侧企稳"
    return candidates.sort_values("score", ascending=False).head(2)

# 回测
log("\n开始回测...")
capital = INITIAL_CAPITAL
all_trades = []
positions = []

df_indexed = df.set_index(["ts_code", "trade_date"]).sort_index()

for i, t_date in enumerate(trading_dates):
    day_df = df[df["trade_date"] == t_date].copy()
    if len(day_df) == 0:
        continue
    
    # 卖出
    day_pnl = 0
    sells_today = []
    new_positions = []
    
    for pos in positions:
        buy_date = pos["buy_date"]
        code = pos["code"]
        buy_price = pos["buy_price"]
        
        hold_days = len([d for d in trading_dates if buy_date < d <= t_date])
        
        if hold_days == 0:
            new_positions.append(pos)
            continue
        
        try:
            today_bar = df_indexed.loc[(code, t_date)]
        except KeyError:
            new_positions.append(pos)
            continue
        
        open_price = today_bar["open"]
        high = today_bar["high"]
        low = today_bar["low"]
        
        should_sell = False
        sell_price = open_price
        reason = ""
        pnl_pct = 0
        
        if hold_days == 1:
            pnl_pct = (open_price - buy_price) / buy_price
            if pnl_pct >= TAKE_PROFIT_T1:
                should_sell = True
                sell_price = open_price
                reason = f"T+1止盈+{pnl_pct*100:.1f}%"
        elif hold_days >= 2:
            if low <= buy_price * (1 + STOP_LOSS):
                should_sell = True
                sell_price = buy_price * (1 + STOP_LOSS)
                pnl_pct = (sell_price - buy_price) / buy_price
                reason = f"T+{hold_days}止损{pnl_pct*100:.1f}%"
            elif high >= buy_price * (1 + TAKE_PROFIT):
                should_sell = True
                sell_price = buy_price * (1 + TAKE_PROFIT)
                pnl_pct = (sell_price - buy_price) / buy_price
                reason = f"T+{hold_days}止盈+{pnl_pct*100:.1f}%"
            
            if hold_days >= MAX_HOLD and not should_sell:
                should_sell = True
                sell_price = open_price
                reason = f"T+{hold_days}到期"
                pnl_pct = (sell_price - buy_price) / buy_price
        
        if should_sell:
            shares = pos["shares"]
            proceeds = shares * sell_price
            pnl_amt = proceeds - pos["cost"]
            day_pnl += pnl_amt
            
            sells_today.append({
                "buy_date": buy_date,
                "sell_date": t_date,
                "code": code,
                "strategy": pos["strategy"],
                "buy_price": buy_price,
                "sell_price": sell_price,
                "shares": shares,
                "cost": pos["cost"],
                "proceeds": proceeds,
                "pnl_amt": pnl_amt,
                "pnl_pct": pnl_pct,
                "reason": reason
            })
        else:
            new_positions.append(pos)
    
    capital += day_pnl
    positions = new_positions
    
    # 买入 - 最多持仓2只，每只用5%资金
    picks = pd.concat([filter_dip(day_df), filter_stabilize(day_df)])
    if len(picks) > 0 and len(positions) < 2:
        picks = picks.sort_values("score", ascending=False).head(1)
        
        for _, row in picks.iterrows():
            buy_price = row["close"]
            available = capital * 0.05
            shares = int(available / buy_price / MIN_LOTS) * MIN_LOTS
            if shares < MIN_LOTS:
                continue
            
            cost = shares * buy_price
            if cost > capital * 0.08:
                continue
            
            capital -= cost
            positions.append({
                "buy_date": t_date,
                "code": row["ts_code"],
                "strategy": row["strategy"],
                "buy_price": buy_price,
                "shares": shares,
                "cost": cost
            })
    
    all_trades.extend(sells_today)
    
    if len(sells_today) > 0 or (len(picks) > 0 and len(positions) < 3):
        log(f"  {t_date.date()} | 卖出{len(sells_today)}只 盈亏{day_pnl:+.0f} | 持仓{len(positions)}只 | 资金{capital:,.0f}")

# 输出报告
print("\n" + "=" * 80)
print("  双策略回测报告 — 真实数据")
print("=" * 80)

total_trades = len(all_trades)
win_trades = len([t for t in all_trades if t["pnl_amt"] > 0])
loss_trades = len([t for t in all_trades if t["pnl_amt"] <= 0])
win_rate = win_trades / total_trades * 100 if total_trades > 0 else 0
total_pnl = capital - INITIAL_CAPITAL
total_return = total_pnl / INITIAL_CAPITAL * 100

print(f"\n  初始资金:     {INITIAL_CAPITAL:>12,.2f} 元")
print(f"  最终资金:     {capital:>12,.2f} 元")
print(f"  总盈亏:       {total_pnl:>+12,.2f} 元")
print(f"  总收益率:     {total_return:>+11.2f}%")
print(f"  交易笔数:     {total_trades:>12}")
print(f"  盈利笔数:     {win_trades:>12}")
print(f"  亏损笔数:     {loss_trades:>12}")
print(f"  胜率:         {win_rate:>11.1f}%")

print("\n" + "=" * 120)
print("  逐笔交易明细:")
print("=" * 120)
print(f"  {'序号':<5} {'买入日':<12} {'卖出日':<12} {'代码':<12} {'策略':<8} {'买价':>8} {'卖价':>8} {'盈亏%':>8} {'盈亏元':>10} {'原因'}")
print("  " + "─" * 115)

for i, t in enumerate(all_trades):
    print(f"  {i+1:<5} {t['buy_date'].strftime('%Y-%m-%d'):<12} {t['sell_date'].strftime('%Y-%m-%d'):<12} {t['code']:<12} {t['strategy']:<8} {t['buy_price']:>8.2f} {t['sell_price']:>8.2f} {t['pnl_pct']:>+7.2f}% {t['pnl_amt']:>+10,.0f} {t['reason']}")

# 保存到文件
output_file = "d:\\pythonProject\\openclaw-quant-system\\diag_key_stocks_out.txt"
with open(output_file, "w", encoding="utf-8") as f:
    f.write("=" * 80 + "\n")
    f.write("  双策略回测报告 — 真实数据\n")
    f.write("=" * 80 + "\n\n")
    f.write(f"  初始资金:     {INITIAL_CAPITAL:>12,.2f} 元\n")
    f.write(f"  最终资金:     {capital:>12,.2f} 元\n")
    f.write(f"  总盈亏:       {total_pnl:>+12,.2f} 元\n")
    f.write(f"  总收益率:     {total_return:>+11.2f}%\n")
    f.write(f"  交易笔数:     {total_trades:>12}\n")
    f.write(f"  盈利笔数:     {win_trades:>12}\n")
    f.write(f"  亏损笔数:     {loss_trades:>12}\n")
    f.write(f"  胜率:         {win_rate:>11.1f}%\n")
    f.write("\n" + "=" * 120 + "\n")
    f.write("  逐笔交易明细:\n")
    f.write("=" * 120 + "\n")
    f.write(f"  {'序号':<5} {'买入日':<12} {'卖出日':<12} {'代码':<12} {'策略':<8} {'买价':>8} {'卖价':>8} {'盈亏%':>8} {'盈亏元':>10} {'原因'}\n")
    f.write("  " + "─" * 115 + "\n")
    for i, t in enumerate(all_trades):
        f.write(f"  {i+1:<5} {t['buy_date'].strftime('%Y-%m-%d'):<12} {t['sell_date'].strftime('%Y-%m-%d'):<12} {t['code']:<12} {t['strategy']:<8} {t['buy_price']:>8.2f} {t['sell_price']:>8.2f} {t['pnl_pct']:>+7.2f}% {t['pnl_amt']:>+10,.0f} {t['reason']}\n")

print(f"\n结果已保存到: {output_file}")
