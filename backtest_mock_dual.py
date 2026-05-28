"""
双策略完整回测 - 使用模拟数据
"""
import pandas as pd
import numpy as np
import pickle
import time
from datetime import datetime, timedelta

# 加载模拟数据
CACHE_FILE = r'd:\pythonProject\openclaw-quant-system\mock_data.pkl'
OUTPUT_FILE = r'd:\pythonProject\openclaw-quant-system\diag_key_stocks_out.txt'

print("加载模拟数据...")
with open(CACHE_FILE, "rb") as f:
    df = pickle.load(f)
print(f"✓ 加载 {len(df)} 条数据, {df['ts_code'].nunique()} 只股票")

# 策略参数 - 优化版v8（只做右侧企稳）
INITIAL_CAPITAL = 1_000_000
STOP_LOSS = -0.02  # -2%止损
TAKE_PROFIT_T1 = 0.05  # T+1日5%止盈
TAKE_PROFIT = 0.10  # T+2及以后10%止盈
MAX_HOLD = 5  # 最大持股5天
MIN_LOTS = 100  # 最少100股
MAX_PICKS_PER_DAY = 3  # 每天最多选3只

# 右侧企稳策略参数 - 严格标准
STABILIZE_PCT_MIN = 3.0
STABILIZE_VOL_RATIO_MIN = 2.0
STABILIZE_TURN_MIN = 2.0
STABILIZE_TURN_MAX = 12.0
STABILIZE_MIN_AMOUNT = 50_000_000

# 回测区间
START_DATE = "2025-05-01"
END_DATE = "2026-05-25"

# 过滤回测区间
df = df[(df["trade_date"] >= START_DATE) & (df["trade_date"] <= END_DATE)].copy()

# 计算指标
print("\n计算指标...")
df["ma5"] = df.groupby("ts_code")["close"].transform(
    lambda x: x.rolling(5, min_periods=5).mean().shift(1)
)
df["ma20"] = df.groupby("ts_code")["close"].transform(
    lambda x: x.rolling(20, min_periods=20).mean().shift(1)
)

avg_vol = df.groupby("ts_code")["volume"].transform(
    lambda x: x.rolling(10, min_periods=5).mean().shift(1)
)
df["vol_ratio"] = df["volume"] / avg_vol

def calc_down_days(pct_series):
    down_days = [0] * len(pct_series)
    for i in range(len(pct_series)):
        if pct_series.iloc[i] < 0:
            down_days[i] = down_days[i - 1] + 1 if i > 0 else 1
        else:
            down_days[i] = 0
    return pd.Series(down_days, index=pct_series.index)

df["down_days"] = df.groupby("ts_code")["pct_chg"].transform(calc_down_days)

def calc_no_new_low(close_series, low_series):
    no_new_low = [0] * len(close_series)
    min_low = float('inf')
    for i in range(len(close_series)):
        if low_series.iloc[i] < min_low:
            min_low = low_series.iloc[i]
            no_new_low[i] = 0
        else:
            no_new_low[i] = no_new_low[i - 1] + 1 if i > 0 else 1
    return pd.Series(no_new_low, index=close_series.index)

df["no_new_low_days"] = df.groupby("ts_code").apply(
    lambda g: calc_no_new_low(g["close"], g["low"])
).reset_index(level=0, drop=True)

print("✓ 指标计算完成")

# 策略筛选函数
def filter_dip(day_df):
    """低吸策略筛选 - 优化版v6"""
    mask = (
        (day_df["pct_chg"] >= DIP_PCT_LO) &
        (day_df["pct_chg"] <= DIP_PCT_HI) &
        (day_df["vol_ratio"] <= DIP_VOL_RATIO_MAX) &
        (day_df["vol_ratio"] > 0) &
        (day_df["ma20"].notna()) &
        (day_df["ma5"].notna()) &
        (day_df["ma5"] > day_df["ma20"]) &
        (day_df["close"] >= day_df["ma20"] * (1 - DIP_MA20_TOLERANCE)) &
        (day_df["close"] <= day_df["ma20"] * (1 + DIP_MA20_TOLERANCE)) &
        (day_df["close"] < day_df["ma5"] * (1 - DIP_MA5_DEVIATION)) &
        (day_df["down_days"] >= 2) &
        (day_df["turnover_rate"] >= DIP_TURN_MIN) &
        (day_df["turnover_rate"] <= DIP_TURN_MAX) &
        (day_df["amount"] >= DIP_MIN_AMOUNT) &
        (day_df["close"] > 5.0)
    )
    candidates = day_df[mask].copy()
    if len(candidates) == 0:
        return candidates
    
    candidates["score"] = (
        (candidates["pct_chg"] - DIP_PCT_LO) / (DIP_PCT_HI - DIP_PCT_LO) * 30 +
        (1 - candidates["vol_ratio"] / DIP_VOL_RATIO_MAX) * 30 +
        (candidates["ma5"] - candidates["close"]) / candidates["ma5"] * 100 * 2 +
        candidates["down_days"] * 5
    )
    candidates["strategy"] = "低吸"
    return candidates.sort_values("score", ascending=False).head(MAX_PICKS_PER_DAY)

def filter_stabilize(day_df):
    """右侧企稳策略筛选 - 优化版v11"""
    mask = (
        (day_df["no_new_low_days"] >= 3) &
        (day_df["pct_chg"] >= STABILIZE_PCT_MIN) &
        (day_df["pct_chg"] <= 7.0) &
        (day_df["vol_ratio"] >= STABILIZE_VOL_RATIO_MIN) &
        (day_df["vol_ratio"] <= 5.0) &
        (day_df["ma5"].notna()) &
        (day_df["ma20"].notna()) &
        (day_df["ma5"] > day_df["ma20"]) &
        (day_df["close"] > day_df["ma5"] * 1.01) &
        (day_df["close"] < day_df["ma5"] * 1.08) &
        (day_df["turnover_rate"] >= STABILIZE_TURN_MIN) &
        (day_df["turnover_rate"] <= STABILIZE_TURN_MAX) &
        (day_df["amount"] >= STABILIZE_MIN_AMOUNT) &
        (day_df["close"] > 5.0)
    )
    candidates = day_df[mask].copy()
    if len(candidates) == 0:
        return candidates
    
    candidates["score"] = (
        (candidates["pct_chg"] - STABILIZE_PCT_MIN) * 10 +
        (candidates["vol_ratio"] - STABILIZE_VOL_RATIO_MIN) * 10 +
        candidates["no_new_low_days"] * 5
    )
    candidates["strategy"] = "右侧企稳"
    return candidates.sort_values("score", ascending=False).head(MAX_PICKS_PER_DAY)

# 卖出模拟
def simulate_sell(buy_price, daily_bars, max_hold=3):
    """
    优化卖出逻辑:
    - T+1日: 盈利>=5%止盈, 亏损>=2%止损
    - T+2日: 盈利>=8%止盈, 亏损>=2%止损
    - T+3及以后: 盈利>=10%止盈, 亏损>=2%止损, 或移动止盈
    - 最大持股3天
    """
    highest_price = buy_price  # 记录最高价用于移动止盈
    
    for i, bar in enumerate(daily_bars):
        hold_days = i + 1  # T+1, T+2, ...
        open_price = bar["open"]
        high = bar["high"]
        low = bar["low"]
        
        # 更新最高价
        highest_price = max(highest_price, high)
        
        if hold_days == 1:
            # T+1日: 快速止盈止损
            pnl_pct = (open_price - buy_price) / buy_price
            if pnl_pct >= TAKE_PROFIT_T1:
                return open_price, f"T+1止盈+{pnl_pct*100:.1f}%", pnl_pct
            # 检查止损
            if low <= buy_price * (1 + STOP_LOSS):
                sell_price = buy_price * (1 + STOP_LOSS)
                pnl = (sell_price - buy_price) / buy_price
                return sell_price, f"T+1止损{pnl*100:.1f}%", pnl
            # 继续持有
        elif hold_days == 2:
            # T+2日: 移动止盈
            # 如果已有盈利，设置移动止损在成本价
            if highest_price > buy_price * 1.03:  # 曾盈利3%以上
                # 移动止损：从最高点回撤2%就卖出
                if low <= highest_price * 0.98:
                    sell_price = highest_price * 0.98
                    pnl = (sell_price - buy_price) / buy_price
                    return sell_price, f"T+2移动止盈+{pnl*100:.1f}%", pnl
            # 检查止损
            if low <= buy_price * (1 + STOP_LOSS):
                sell_price = buy_price * (1 + STOP_LOSS)
                pnl = (sell_price - buy_price) / buy_price
                return sell_price, f"T+2止损{pnl*100:.1f}%", pnl
            # 检查止盈
            if high >= buy_price * (1 + TAKE_PROFIT):
                sell_price = buy_price * (1 + TAKE_PROFIT)
                pnl = (sell_price - buy_price) / buy_price
                return sell_price, f"T+2止盈+{pnl*100:.1f}%", pnl
        else:
            # T+3及以后: 更积极的卖出
            # 移动止盈：从最高点回撤3%就卖出
            if highest_price > buy_price * 1.05:  # 曾盈利5%以上
                if low <= highest_price * 0.97:
                    sell_price = highest_price * 0.97
                    pnl = (sell_price - buy_price) / buy_price
                    return sell_price, f"T+{hold_days}移动止盈+{pnl*100:.1f}%", pnl
            # 检查止损
            if low <= buy_price * (1 + STOP_LOSS):
                sell_price = buy_price * (1 + STOP_LOSS)
                pnl = (sell_price - buy_price) / buy_price
                return sell_price, f"T+{hold_days}止损{pnl*100:.1f}%", pnl
            # 检查止盈
            if high >= buy_price * (1 + TAKE_PROFIT):
                sell_price = buy_price * (1 + TAKE_PROFIT)
                pnl = (sell_price - buy_price) / buy_price
                return sell_price, f"T+{hold_days}止盈+{pnl*100:.1f}%", pnl
            # 最大持股天数
            if hold_days >= max_hold:
                return open_price, f"T+{hold_days}到期", (open_price - buy_price) / buy_price
    
    # 如果没有卖出（数据不足）
    last_bar = daily_bars[-1]
    return last_bar["close"], "数据不足", (last_bar["close"] - buy_price) / buy_price

# 运行回测
print("\n" + "=" * 80)
print("  双策略回测 — 低吸策略 + 右侧企稳策略")
print(f"  回测区间: {START_DATE} ~ {END_DATE}")
print(f"  初始资金: {INITIAL_CAPITAL/10000:.0f}万元 | 止损{STOP_LOSS*100:.0f}% | 最大持股{MAX_HOLD}天")
print("=" * 80)

trading_dates = sorted(df["trade_date"].unique())
print(f"\n总交易日: {len(trading_dates)}")

capital = INITIAL_CAPITAL
initial_capital = INITIAL_CAPITAL  # 保存初始资金
all_trades = []
daily_summary = []
positions = []  # 当前持仓

# 创建快速查找字典
df_indexed = df.set_index(["ts_code", "trade_date"]).sort_index()

for i, t_date in enumerate(trading_dates):
    day_df = df[df["trade_date"] == t_date].copy()
    if len(day_df) == 0:
        continue
    
    # 1. 先处理卖出
    day_pnl = 0
    sells_today = []
    new_positions = []
    
    for pos in positions:
        buy_date = pos["buy_date"]
        code = pos["code"]
        buy_price = pos["buy_price"]
        
        # 计算持股天数
        hold_days = len([d for d in trading_dates if buy_date < d <= t_date])
        
        if hold_days == 0:
            new_positions.append(pos)
            continue
        
        # 获取今天的K线
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
        
        # 获取持仓最高价（如果有记录）
        highest = pos.get("highest", buy_price)
        highest = max(highest, high)
        
        if hold_days == 1:
            # T+1日: 快速止盈止损
            pnl_pct = (open_price - buy_price) / buy_price
            if pnl_pct >= TAKE_PROFIT_T1:
                should_sell = True
                sell_price = open_price
                reason = f"T+1止盈+{pnl_pct*100:.1f}%"
            # 检查止损（放宽到-3%）
            elif low <= buy_price * 0.97:
                should_sell = True
                sell_price = buy_price * 0.97
                pnl_pct = (sell_price - buy_price) / buy_price
                reason = f"T+1止损{pnl_pct*100:.1f}%"
            # 继续持有，更新最高价
            else:
                pos["highest"] = highest
                new_positions.append(pos)
                continue
                
        elif hold_days == 2:
            # T+2日: 移动止盈（提高阈值）
            if highest > buy_price * 1.05:  # 曾盈利5%以上才启用移动止盈
                # 移动止损：从最高点回撤3%就卖出
                if low <= highest * 0.97:
                    should_sell = True
                    sell_price = highest * 0.97
                    pnl_pct = (sell_price - buy_price) / buy_price
                    reason = f"T+2移动止盈+{pnl_pct*100:.1f}%"
            # 检查止损
            if not should_sell and low <= buy_price * (1 + STOP_LOSS):
                should_sell = True
                sell_price = buy_price * (1 + STOP_LOSS)
                pnl_pct = (sell_price - buy_price) / buy_price
                reason = f"T+2止损{pnl_pct*100:.1f}%"
            # 检查止盈
            elif not should_sell and high >= buy_price * (1 + TAKE_PROFIT):
                should_sell = True
                sell_price = buy_price * (1 + TAKE_PROFIT)
                pnl_pct = (sell_price - buy_price) / buy_price
                reason = f"T+2止盈+{pnl_pct*100:.1f}%"
            # 继续持有
            if not should_sell:
                pos["highest"] = highest
                new_positions.append(pos)
                continue
                
        elif hold_days == 3:
            # T+3日: 移动止盈
            if highest > buy_price * 1.05:  # 曾盈利5%以上
                if low <= highest * 0.96:  # 回撤4%
                    should_sell = True
                    sell_price = highest * 0.96
                    pnl_pct = (sell_price - buy_price) / buy_price
                    reason = f"T+3移动止盈+{pnl_pct*100:.1f}%"
            # 检查止损
            if not should_sell and low <= buy_price * (1 + STOP_LOSS):
                should_sell = True
                sell_price = buy_price * (1 + STOP_LOSS)
                pnl_pct = (sell_price - buy_price) / buy_price
                reason = f"T+3止损{pnl_pct*100:.1f}%"
            # 检查止盈
            elif not should_sell and high >= buy_price * (1 + TAKE_PROFIT):
                should_sell = True
                sell_price = buy_price * (1 + TAKE_PROFIT)
                pnl_pct = (sell_price - buy_price) / buy_price
                reason = f"T+3止盈+{pnl_pct*100:.1f}%"
            # 继续持有
            if not should_sell:
                pos["highest"] = highest
                new_positions.append(pos)
                continue
                
        else:
            # T+4及以后: 更积极的卖出
            if highest > buy_price * 1.05:  # 曾盈利5%以上
                if low <= highest * 0.95:  # 回撤5%
                    should_sell = True
                    sell_price = highest * 0.95
                    pnl_pct = (sell_price - buy_price) / buy_price
                    reason = f"T+{hold_days}移动止盈+{pnl_pct*100:.1f}%"
            # 检查止损
            if not should_sell and low <= buy_price * (1 + STOP_LOSS):
                should_sell = True
                sell_price = buy_price * (1 + STOP_LOSS)
                pnl_pct = (sell_price - buy_price) / buy_price
                reason = f"T+{hold_days}止损{pnl_pct*100:.1f}%"
            # 检查止盈
            elif not should_sell and high >= buy_price * (1 + TAKE_PROFIT):
                should_sell = True
                sell_price = buy_price * (1 + TAKE_PROFIT)
                pnl_pct = (sell_price - buy_price) / buy_price
                reason = f"T+{hold_days}止盈+{pnl_pct*100:.1f}%"
            # 最大持股天数
            elif hold_days >= MAX_HOLD and not should_sell:
                should_sell = True
                sell_price = open_price
                reason = f"T+{hold_days}到期"
                pnl_pct = (sell_price - buy_price) / buy_price
        
        if should_sell:
            shares = pos["shares"]
            proceeds = shares * sell_price
            pnl_amt = proceeds - pos["cost"]
            day_pnl += proceeds  # Add full proceeds back to capital
            
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
    
    # 2. 再处理买入 - 修复资金管理
    # 使用固定金额交易，每笔5万元
    # 最多持仓3只
    FIXED_TRADE_AMOUNT = 50000  # 每笔固定交易金额
    
    if len(positions) < 3:
        picks = filter_stabilize(day_df)
        if len(picks) > 0:
            # 每天最多买1只
            picks = picks.sort_values("score", ascending=False).head(1)
            
            for _, row in picks.iterrows():
                if len(positions) >= 3:
                    break
                    
                buy_price = row["close"]
                
                # 固定金额交易
                shares = int(FIXED_TRADE_AMOUNT / buy_price / MIN_LOTS) * MIN_LOTS
                if shares < MIN_LOTS:
                    continue
                
                cost = shares * buy_price
                
                # 检查是否有足够资金
                if cost > capital * 0.9:  # 保留10%现金
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
    daily_summary.append({
        "date": t_date,
        "trades": len(sells_today),
        "pnl": day_pnl,
        "capital": capital,
        "positions": len(positions)
    })
    
    if len(sells_today) > 0 or (len(positions) < 3 and len(picks) > 0 if 'picks' in locals() else False):
        print(f"  {t_date.date()} | 卖出{len(sells_today)}只 盈亏{day_pnl:+.0f} | 买入{len(picks) if len(positions) < 3 else 0}只 | 持仓{len(positions)}只 | 资金{capital:,.0f}")

# 输出报告
print("\n" + "=" * 80)
print("  双策略回测报告")
print("=" * 80)

total_trades = len(all_trades)
win_trades = len([t for t in all_trades if t["pnl_pct"] > 0])
loss_trades = total_trades - win_trades
win_rate = win_trades / total_trades * 100 if total_trades > 0 else 0

total_pnl = sum(t["pnl_amt"] for t in all_trades)
total_return = (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
avg_pnl_pct = np.mean([t["pnl_pct"] for t in all_trades]) if total_trades > 0 else 0

max_win = max([t["pnl_pct"] for t in all_trades], default=0) * 100
max_loss = min([t["pnl_pct"] for t in all_trades], default=0) * 100

# 按策略统计
dip_trades = [t for t in all_trades if t["strategy"] == "低吸"]
stab_trades = [t for t in all_trades if t["strategy"] == "右侧企稳"]

print(f"\n  初始资金:     {INITIAL_CAPITAL:>12,.2f} 元")
print(f"  最终资金:     {capital:>12,.2f} 元")
print(f"  总盈亏:       {total_pnl:>+12,.2f} 元")
print(f"  总收益率:     {total_return:>+12.2f}%")
print(f"  交易笔数:     {total_trades:>12}")
print(f"  盈利笔数:     {win_trades:>12}")
print(f"  亏损笔数:     {loss_trades:>12}")
print(f"  胜率:         {win_rate:>11.1f}%")
print(f"  平均每笔:     {avg_pnl_pct:>+11.2f}%")
print(f"  最大单笔盈利: {max_win:>+11.2f}%")
print(f"  最大单笔亏损: {max_loss:>+11.2f}%")

print(f"\n  按策略统计:")
print(f"    低吸策略:   {len(dip_trades)} 笔, 胜率 {len([t for t in dip_trades if t['pnl_pct'] > 0]) / len(dip_trades) * 100 if len(dip_trades) > 0 else 0:.1f}%")
print(f"    右侧企稳:   {len(stab_trades)} 笔, 胜率 {len([t for t in stab_trades if t['pnl_pct'] > 0]) / len(stab_trades) * 100 if len(stab_trades) > 0 else 0:.1f}%")

# 逐笔交易明细
print(f"\n{'='*120}")
print(f"  逐笔交易明细:")
print(f"{'='*120}")
print(f"{'序号':>4} {'买入日':<12} {'卖出日':<12} {'代码':<12} {'策略':<8} {'买价':>8} {'卖价':>8} {'盈亏%':>8} {'盈亏元':>10} {'原因':<20}")
print(f"{'─'*4} {'─'*12} {'─'*12} {'─'*12} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*10} {'─'*20}")

for idx, t in enumerate(all_trades):
    print(f"{idx+1:>4} {t['buy_date'].strftime('%Y-%m-%d'):<12} {t['sell_date'].strftime('%Y-%m-%d'):<12} {t['code']:<12} {t['strategy']:<8} {t['buy_price']:>8.2f} {t['sell_price']:>8.2f} {t['pnl_pct']*100:>+7.2f}% {t['pnl_amt']:>+10,.0f} {t['reason']:<20}")

# 保存结果
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write("=" * 80 + "\n")
    f.write("  双策略回测报告 — 模拟数据\n")
    f.write("=" * 80 + "\n\n")
    f.write(f"  初始资金:     {INITIAL_CAPITAL:>12,.2f} 元\n")
    f.write(f"  最终资金:     {capital:>12,.2f} 元\n")
    f.write(f"  总盈亏:       {total_pnl:>+12,.2f} 元\n")
    f.write(f"  总收益率:     {total_return:>+12.2f}%\n")
    f.write(f"  交易笔数:     {total_trades:>12}\n")
    f.write(f"  盈利笔数:     {win_trades:>12}\n")
    f.write(f"  亏损笔数:     {loss_trades:>12}\n")
    f.write(f"  胜率:         {win_rate:>11.1f}%\n")
    f.write(f"  平均每笔:     {avg_pnl_pct:>+11.2f}%\n")
    f.write(f"  最大单笔盈利: {max_win:>+11.2f}%\n")
    f.write(f"  最大单笔亏损: {max_loss:>+11.2f}%\n\n")
    
    f.write(f"{'='*120}\n")
    f.write(f"  逐笔交易明细:\n")
    f.write(f"{'='*120}\n")
    f.write(f"{'序号':>4} {'买入日':<12} {'卖出日':<12} {'代码':<12} {'策略':<8} {'买价':>8} {'卖价':>8} {'盈亏%':>8} {'盈亏元':>10} {'原因':<20}\n")
    f.write(f"{'─'*4} {'─'*12} {'─'*12} {'─'*12} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*10} {'─'*20}\n")
    
    for idx, t in enumerate(all_trades):
        f.write(f"{idx+1:>4} {t['buy_date'].strftime('%Y-%m-%d'):<12} {t['sell_date'].strftime('%Y-%m-%d'):<12} {t['code']:<12} {t['strategy']:<8} {t['buy_price']:>8.2f} {t['sell_price']:>8.2f} {t['pnl_pct']*100:>+7.2f}% {t['pnl_amt']:>+10,.0f} {t['reason']:<20}\n")

print(f"\n结果已保存到: {OUTPUT_FILE}")
