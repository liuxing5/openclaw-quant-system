"""
双策略真实数据库回测 - 右侧企稳策略
================================================
回测区间: 2025-05-01 ~ 2026-05-25
资金管理: 固定金额5万元/笔，最多持仓3只
止损: -2%
止盈: T+1日5%，T+2及以后10%
最大持股: 5天
"""
import psycopg2
import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor
import time
from datetime import datetime, timedelta

# 数据库连接 - 使用transaction pooler端口
DB_URL = "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"

# 策略参数
INITIAL_CAPITAL = 1_000_000
STOP_LOSS = -0.02  # -2%止损
TAKE_PROFIT_T1 = 0.05  # T+1日5%止盈
TAKE_PROFIT = 0.10  # T+2及以后10%止盈
MAX_HOLD = 5  # 最大持股5天
MIN_LOTS = 100  # 最少100股
MAX_PICKS_PER_DAY = 3  # 每天最多选3只
FIXED_TRADE_AMOUNT = 50000  # 每笔固定交易金额

# 右侧企稳策略参数
STABILIZE_PCT_MIN = 3.0
STABILIZE_VOL_RATIO_MIN = 2.0
STABILIZE_TURN_MIN = 2.0
STABILIZE_TURN_MAX = 12.0
STABILIZE_MIN_AMOUNT = 50_000_000

# 回测区间
START_DATE = "2025-05-01"
END_DATE = "2026-05-25"

OUTPUT_FILE = r'd:\pythonProject\openclaw-quant-system\backtest_real_db_result.txt'

_out = None

def log(msg="", end="\n"):
    global _out
    print(msg, end=end, flush=True)
    if _out:
        _out.write(msg + end)
        _out.flush()


def load_data_chunked(start_date, end_date, lookback_days=40, chunk_days=7):
    """分批加载数据，使用更小的批次避免连接问题"""
    from datetime import datetime, timedelta
    
    sd = datetime.strptime(start_date, "%Y-%m-%d")
    ed = datetime.strptime(end_date, "%Y-%m-%d")
    lookback_start = (sd - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    
    log(f"步骤1: 从数据库分批加载数据 {start_date} ~ {end_date} (前置数据从{lookback_start}开始)...")
    t0 = time.time()
    
    # 生成周区间
    chunks = []
    current = sd - timedelta(days=lookback_days)
    while current <= ed:
        chunk_end = min(current + timedelta(days=chunk_days-1), ed)
        chunks.append((current.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        current = chunk_end + timedelta(days=1)
    
    log(f"  分为{len(chunks)}个批次加载（每批{chunk_days}天）...")
    
    all_dfs = []
    success_count = 0
    fail_count = 0
    
    for chunk_start, chunk_end in chunks:
        conn = None
        try:
            conn = psycopg2.connect(DB_URL, connect_timeout=10)
            conn.autocommit = True
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SET statement_timeout = '30s'")
            
            cur.execute("""
                SELECT ts_code, trade_date, open, high, low, close,
                       volume, amount, pct_chg, turnover_rate
                FROM daily_quotes
                WHERE trade_date >= %s AND trade_date <= %s
                  AND pct_chg IS NOT NULL
                  AND amount IS NOT NULL
                  AND turnover_rate IS NOT NULL
                ORDER BY ts_code, trade_date
            """, (chunk_start, chunk_end))
            
            rows = cur.fetchall()
            cur.close()
            conn.close()
            
            if rows:
                df_chunk = pd.DataFrame(rows)
                all_dfs.append(df_chunk)
                success_count += 1
                if success_count % 5 == 0:
                    log(f"  进度: {success_count}/{len(chunks)} 批次成功, 已加载{sum(len(d) for d in all_dfs)}条")
            else:
                log(f"  - {chunk_start} ~ {chunk_end}: 无数据")
                
        except Exception as e:
            fail_count += 1
            log(f"  ✗ {chunk_start} ~ {chunk_end} 失败: {str(e)[:60]}")
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
            # 每个批次之间等待，让连接池释放
            time.sleep(1)
    
    if not all_dfs:
        raise Exception("没有成功加载任何数据")
    
    df = pd.concat(all_dfs, ignore_index=True)
    
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    df = df.dropna(subset=["close", "pct_chg", "amount", "turnover_rate"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    
    log(f"\n  ✓ 总计: {len(df)} 条, {df['ts_code'].nunique()} 只股票")
    log(f"  成功批次: {success_count}/{len(chunks)}, 失败批次: {fail_count}")
    log(f"  日期范围: {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")
    log(f"  总耗时: {time.time()-t0:.1f}s")
    return df


def compute_indicators(df):
    """计算技术指标"""
    log("\n步骤2: 计算技术指标...")
    t0 = time.time()
    
    # MA5
    df["ma5"] = df.groupby("ts_code")["close"].transform(
        lambda x: x.rolling(5, min_periods=5).mean().shift(1)
    )
    
    # MA20
    df["ma20"] = df.groupby("ts_code")["close"].transform(
        lambda x: x.rolling(20, min_periods=20).mean().shift(1)
    )
    
    # 量比
    avg_vol = df.groupby("ts_code")["volume"].transform(
        lambda x: x.rolling(10, min_periods=5).mean().shift(1)
    )
    df["vol_ratio"] = df["volume"] / avg_vol
    
    # 连续下跌天数
    def calc_down_days(pct_series):
        down_days = [0] * len(pct_series)
        for i in range(len(pct_series)):
            if pct_series.iloc[i] < 0:
                down_days[i] = down_days[i - 1] + 1 if i > 0 else 1
            else:
                down_days[i] = 0
        return pd.Series(down_days, index=pct_series.index)
    
    df["down_days"] = df.groupby("ts_code")["pct_chg"].transform(calc_down_days)
    
    # 连续不创新低天数
    def calc_no_new_low(group):
        close_series = group["close"].values
        low_series = group["low"].values
        no_new_low = np.zeros(len(group), dtype=int)
        min_low = float('inf')
        for i in range(len(group)):
            if low_series[i] < min_low:
                min_low = low_series[i]
                no_new_low[i] = 0
            else:
                no_new_low[i] = no_new_low[i - 1] + 1 if i > 0 else 1
        return pd.Series(no_new_low, index=group.index)
    
    df["no_new_low_days"] = df.groupby("ts_code", group_keys=False).apply(calc_no_new_low)
    
    log(f"  ✓ 指标计算完成, 耗时{time.time()-t0:.1f}s")
    return df


def filter_stabilize(day_df):
    """右侧企稳策略筛选"""
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


def run_backtest(df):
    """运行回测"""
    log("\n" + "="*80)
    log("双策略回测 — 右侧企稳策略 (真实数据库)")
    log(f"回测区间: {START_DATE} ~ {END_DATE}")
    log(f"初始资金: 100万元 | 止损-2% | 最大持股{MAX_HOLD}天")
    log("="*80)
    
    # 过滤回测区间
    df_bt = df[(df["trade_date"] >= START_DATE) & (df["trade_date"] <= END_DATE)].copy()
    
    # 获取所有交易日
    trading_dates = sorted(df_bt["trade_date"].unique())
    log(f"\n总交易日: {len(trading_dates)}")
    
    # 创建索引加速查询
    df_indexed = df_bt.set_index(["ts_code", "trade_date"])
    
    # 初始化
    capital = INITIAL_CAPITAL
    positions = []  # 持仓列表
    trades = []  # 交易记录
    
    for t_date in trading_dates:
        day_df = df_bt[df_bt["trade_date"] == t_date]
        
        # 1. 先处理卖出
        new_positions = []
        sell_count = 0
        sell_pnl = 0
        
        for pos in positions:
            buy_date = pos["buy_date"]
            code = pos["code"]
            buy_price = pos["buy_price"]
            
            # 计算持股天数
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
            
            highest = pos.get("highest", buy_price)
            highest = max(highest, high)
            
            if hold_days == 1:
                pnl_pct = (open_price - buy_price) / buy_price
                if pnl_pct >= TAKE_PROFIT_T1:
                    should_sell = True
                    sell_price = open_price
                    reason = f"T+1止盈+{pnl_pct*100:.1f}%"
                elif low <= buy_price * (1 + STOP_LOSS):
                    should_sell = True
                    sell_price = buy_price * (1 + STOP_LOSS)
                    pnl_pct = (sell_price - buy_price) / buy_price
                    reason = f"T+1止损{pnl_pct*100:.1f}%"
                else:
                    pos["highest"] = highest
                    new_positions.append(pos)
                    continue
                    
            elif hold_days == 2:
                if highest > buy_price * 1.05:
                    if low <= highest * 0.97:
                        should_sell = True
                        sell_price = highest * 0.97
                        pnl_pct = (sell_price - buy_price) / buy_price
                        reason = f"T+2移动止盈+{pnl_pct*100:.1f}%"
                if not should_sell and low <= buy_price * (1 + STOP_LOSS):
                    should_sell = True
                    sell_price = buy_price * (1 + STOP_LOSS)
                    pnl_pct = (sell_price - buy_price) / buy_price
                    reason = f"T+2止损{pnl_pct*100:.1f}%"
                elif not should_sell and high >= buy_price * (1 + TAKE_PROFIT):
                    should_sell = True
                    sell_price = buy_price * (1 + TAKE_PROFIT)
                    pnl_pct = (sell_price - buy_price) / buy_price
                    reason = f"T+2止盈+{pnl_pct*100:.1f}%"
                if not should_sell:
                    pos["highest"] = highest
                    new_positions.append(pos)
                    continue
                    
            elif hold_days == 3:
                if highest > buy_price * 1.05:
                    if low <= highest * 0.96:
                        should_sell = True
                        sell_price = highest * 0.96
                        pnl_pct = (sell_price - buy_price) / buy_price
                        reason = f"T+3移动止盈+{pnl_pct*100:.1f}%"
                if not should_sell and low <= buy_price * (1 + STOP_LOSS):
                    should_sell = True
                    sell_price = buy_price * (1 + STOP_LOSS)
                    pnl_pct = (sell_price - buy_price) / buy_price
                    reason = f"T+3止损{pnl_pct*100:.1f}%"
                elif not should_sell and high >= buy_price * (1 + TAKE_PROFIT):
                    should_sell = True
                    sell_price = buy_price * (1 + TAKE_PROFIT)
                    pnl_pct = (sell_price - buy_price) / buy_price
                    reason = f"T+3止盈+{pnl_pct*100:.1f}%"
                if not should_sell:
                    pos["highest"] = highest
                    new_positions.append(pos)
                    continue
                    
            else:
                if highest > buy_price * 1.05:
                    if low <= highest * 0.95:
                        should_sell = True
                        sell_price = highest * 0.95
                        pnl_pct = (sell_price - buy_price) / buy_price
                        reason = f"T+{hold_days}移动止盈+{pnl_pct*100:.1f}%"
                if not should_sell and low <= buy_price * (1 + STOP_LOSS):
                    should_sell = True
                    sell_price = buy_price * (1 + STOP_LOSS)
                    pnl_pct = (sell_price - buy_price) / buy_price
                    reason = f"T+{hold_days}止损{pnl_pct*100:.1f}%"
                elif not should_sell and high >= buy_price * (1 + TAKE_PROFIT):
                    should_sell = True
                    sell_price = buy_price * (1 + TAKE_PROFIT)
                    pnl_pct = (sell_price - buy_price) / buy_price
                    reason = f"T+{hold_days}止盈+{pnl_pct*100:.1f}%"
                elif hold_days >= MAX_HOLD and not should_sell:
                    should_sell = True
                    sell_price = open_price
                    reason = f"T+{hold_days}到期"
                    pnl_pct = (sell_price - buy_price) / buy_price
            
            if should_sell:
                shares = pos["shares"]
                proceeds = shares * sell_price
                cost = shares * buy_price
                pnl = proceeds - cost
                sell_pnl += pnl
                sell_count += 1
                
                trades.append({
                    "buy_date": buy_date.strftime("%Y-%m-%d"),
                    "sell_date": t_date.strftime("%Y-%m-%d"),
                    "code": code,
                    "strategy": pos["strategy"],
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "pnl_pct": pnl_pct * 100,
                    "pnl": pnl,
                    "reason": reason
                })
            else:
                new_positions.append(pos)
        
        positions = new_positions
        capital += sell_pnl
        
        # 2. 处理买入
        buy_count = 0
        if len(positions) < 3:
            picks = filter_stabilize(day_df)
            if len(picks) > 0:
                picks = picks.sort_values("score", ascending=False).head(1)
                
                for _, row in picks.iterrows():
                    if len(positions) >= 3:
                        break
                        
                    buy_price = row["close"]
                    shares = int(FIXED_TRADE_AMOUNT / buy_price / MIN_LOTS) * MIN_LOTS
                    if shares < MIN_LOTS:
                        continue
                    
                    cost = shares * buy_price
                    if cost > capital * 0.95:
                        continue
                    
                    positions.append({
                        "code": row["ts_code"],
                        "buy_date": t_date,
                        "buy_price": buy_price,
                        "shares": shares,
                        "strategy": row["strategy"],
                        "highest": buy_price
                    })
                    capital -= cost
                    buy_count += 1
        
        # 记录每日状态
        if buy_count > 0 or sell_count > 0 or len(positions) > 0:
            log(f"  {t_date.strftime('%Y-%m-%d')} | 卖出{sell_count}只 盈亏{sell_pnl:+.0f} | 买入{buy_count}只 | 持仓{len(positions)}只 | 资金{capital:,.0f}")
    
    # 输出结果
    log("\n" + "="*80)
    log("回测报告")
    log("="*80)
    
    final_capital = capital
    total_pnl = final_capital - INITIAL_CAPITAL
    total_return = total_pnl / INITIAL_CAPITAL * 100
    
    win_trades = [t for t in trades if t["pnl"] > 0]
    lose_trades = [t for t in trades if t["pnl"] <= 0]
    
    log(f"\n  初始资金:     {INITIAL_CAPITAL:>12,.2f} 元")
    log(f"  最终资金:     {final_capital:>12,.2f} 元")
    log(f"  总盈亏:       {total_pnl:>12,.2f} 元")
    log(f"  总收益率:     {total_return:>10.2f}%")
    log(f"  交易笔数:     {len(trades):>10}")
    log(f"  盈利笔数:     {len(win_trades):>10}")
    log(f"  亏损笔数:     {len(lose_trades):>10}")
    log(f"  胜率:         {len(win_trades)/len(trades)*100 if trades else 0:>9.1f}%")
    
    if trades:
        avg_pnl = np.mean([t["pnl_pct"] for t in trades])
        max_win = max([t["pnl_pct"] for t in trades])
        max_lose = min([t["pnl_pct"] for t in trades])
        log(f"  平均每笔:     {avg_pnl:>10.2f}%")
        log(f"  最大单笔盈利: {max_win:>10.2f}%")
        log(f"  最大单笔亏损: {max_lose:>10.2f}%")
    
    # 按策略统计
    log(f"\n  按策略统计:")
    strategies = set(t["strategy"] for t in trades)
    for strat in strategies:
        strat_trades = [t for t in trades if t["strategy"] == strat]
        strat_wins = len([t for t in strat_trades if t["pnl"] > 0])
        log(f"    {strat}:   {len(strat_trades)} 笔, 胜率 {strat_wins/len(strat_trades)*100 if strat_trades else 0:.1f}%")
    
    # 逐笔交易明细
    log(f"\n{'='*120}")
    log(f"逐笔交易明细:")
    log(f"{'='*120}")
    log(f"{'序号':>4} {'买入日':<12} {'卖出日':<12} {'代码':<14} {'策略':<8} {'买价':>8} {'卖价':>8} {'盈亏%':>8} {'盈亏元':>10} {'原因':<20}")
    log(f"{'─'*4} {'─'*12} {'─'*12} {'─'*14} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*10} {'─'*20}")
    
    for i, t in enumerate(trades, 1):
        log(f"{i:>4} {t['buy_date']:<12} {t['sell_date']:<12} {t['code']:<14} {t['strategy']:<8} {t['buy_price']:>8.2f} {t['sell_price']:>8.2f} {t['pnl_pct']:>+7.2f}% {t['pnl']:>+10,.0f} {t['reason']:<20}")
    
    # 保存到文件
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("="*80 + "\n")
        f.write("双策略回测报告 — 真实数据库数据\n")
        f.write("="*80 + "\n\n")
        f.write(f"  初始资金:     {INITIAL_CAPITAL:>12,.2f} 元\n")
        f.write(f"  最终资金:     {final_capital:>12,.2f} 元\n")
        f.write(f"  总盈亏:       {total_pnl:>12,.2f} 元\n")
        f.write(f"  总收益率:     {total_return:>10.2f}%\n")
        f.write(f"  交易笔数:     {len(trades):>10}\n")
        f.write(f"  盈利笔数:     {len(win_trades):>10}\n")
        f.write(f"  亏损笔数:     {len(lose_trades):>10}\n")
        f.write(f"  胜率:         {len(win_trades)/len(trades)*100 if trades else 0:>9.1f}%\n")
        
        f.write(f"\n{'='*120}\n")
        f.write(f"逐笔交易明细:\n")
        f.write(f"{'='*120}\n")
        f.write(f"{'序号':>4} {'买入日':<12} {'卖出日':<12} {'代码':<14} {'策略':<8} {'买价':>8} {'卖价':>8} {'盈亏%':>8} {'盈亏元':>10} {'原因':<20}\n")
        f.write(f"{'─'*4} {'─'*12} {'─'*12} {'─'*14} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*10} {'─'*20}\n")
        
        for i, t in enumerate(trades, 1):
            f.write(f"{i:>4} {t['buy_date']:<12} {t['sell_date']:<12} {t['code']:<14} {t['strategy']:<8} {t['buy_price']:>8.2f} {t['sell_price']:>8.2f} {t['pnl_pct']:>+7.2f}% {t['pnl']:>+10,.0f} {t['reason']:<20}\n")
    
    log(f"\n结果已保存到: {OUTPUT_FILE}")


def main():
    global _out
    _out = open(OUTPUT_FILE, "w", encoding="utf-8")
    
    try:
        # 分批加载数据
        df = load_data_chunked(START_DATE, END_DATE)
        
        # 计算指标
        df = compute_indicators(df)
        
        # 运行回测
        run_backtest(df)
        
    finally:
        if _out:
            _out.close()


if __name__ == "__main__":
    main()
