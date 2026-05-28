import psycopg2
import time
import sys
import os
import pickle
import pandas as pd
import numpy as np
from psycopg2.extras import RealDictCursor

# 尝试多个连接URL
URLS = [
    # Transaction pooler（先用这个，连接池限制较松）
    "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres",
    # Session pooler
    "postgresql://postgres.qoakbxswwjqfsgbcgepr:wYFBB91zViSrk2vl@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres",
]

CACHE_FILE = r'd:\pythonProject\openclaw-quant-system\data_cache.pkl'
OUTPUT_FILE = r'd:\pythonProject\openclaw-quant-system\diag_key_stocks_out.txt'

_out = None

def log(msg="", end="\n"):
    global _out
    print(msg, end=end, flush=True)
    if _out:
        _out.write(msg + end)
        _out.flush()

def connect():
    for attempt in range(500):
        for url in URLS:
            try:
                log(f"尝试连接 (尝试 {attempt+1})...")
                conn = psycopg2.connect(url, connect_timeout=15, sslmode='require')
                conn.autocommit = True
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
                log("✓ 连接成功!")
                return conn
            except Exception as e:
                err = str(e)
                if "max clients" in err:
                    wait = 15
                    log(f"连接池满, 等待{wait}s...")
                    time.sleep(wait)
                elif "SSL" in err:
                    wait = 5
                    log(f"SSL错误, 等待{wait}s...")
                    time.sleep(wait)
                elif "timeout" in err.lower() or "timed out" in err.lower():
                    wait = 5
                    log(f"超时, 等待{wait}s...")
                    time.sleep(wait)
                else:
                    log(f"错误: {err[:120]}")
                    time.sleep(5)
    raise Exception("无法连接数据库")

def main():
    global _out
    try:
        _out = open(OUTPUT_FILE, "w", encoding="utf-8")
        log("="*80)
        log("  双策略回测 — 低吸策略 + 右侧企稳策略")
        log("  回测区间: 2025-05-01 ~ 2026-05-25")
        log("  初始资金: 100万元 | 止损-2% | 止盈+5% | 最大持股3天")
        log("="*80)
        
        # 加载数据
        if os.path.exists(CACHE_FILE):
            log("从缓存加载数据...")
            with open(CACHE_FILE, "rb") as f:
                df = pickle.load(f)
            log(f"✓ 缓存加载: {len(df)} 条, {df['ts_code'].nunique()} 只股票")
        else:
            log("连接数据库...")
            conn = connect()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            log("查询数据 2025-04-01 ~ 2026-05-25...")
            cur.execute("""
                SELECT ts_code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover_rate
                FROM daily_quotes 
                WHERE trade_date >= '2025-04-01' AND trade_date <= '2026-05-25'
                  AND pct_chg IS NOT NULL AND amount IS NOT NULL AND turnover_rate IS NOT NULL
                ORDER BY ts_code, trade_date
            """)
            rows = cur.fetchall()
            cur.close()
            conn.close()
            log(f"✓ 获取 {len(rows)} 条数据")
            
            df = pd.DataFrame(rows)
            for c in ["open","high","low","close","volume","amount","pct_chg","turnover_rate"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["close","pct_chg","amount","turnover_rate"])
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
            
            with open(CACHE_FILE, "wb") as f:
                pickle.dump(df, f)
            log(f"✓ 缓存保存: {len(df)} 条, {df['ts_code'].nunique()} 只股票")
        
        # 计算指标
        log("\n计算技术指标...")
        df["ma5"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(5, min_periods=5).mean().shift(1))
        df["ma20"] = df.groupby("ts_code")["close"].transform(lambda x: x.rolling(20, min_periods=20).mean().shift(1))
        avg_vol = df.groupby("ts_code")["volume"].transform(lambda x: x.rolling(10, min_periods=5).mean().shift(1))
        df["vol_ratio"] = df["volume"] / avg_vol
        
        def calc_down(pct):
            d = [0]*len(pct)
            for i in range(len(pct)):
                if pct.iloc[i] < 0:
                    d[i] = d[i-1]+1 if i>0 else 1
                else:
                    d[i] = 0
            return pd.Series(d, index=pct.index)
        df["down_days"] = df.groupby("ts_code")["pct_chg"].transform(calc_down)
        
        def calc_nnl(low):
            d = [0]*len(low)
            for i in range(1, len(low)):
                if low.iloc[i] >= low.iloc[i-1]:
                    d[i] = d[i-1]+1
                else:
                    d[i] = 0
            return pd.Series(d, index=low.index)
        df["no_new_low_days"] = df.groupby("ts_code")["low"].transform(calc_nnl)
        log("✓ 指标计算完成")
        
        # 回测函数
        def run_bt(name, filter_fn):
            dates = sorted(df["trade_date"].unique())
            capital = 1_000_000.0
            trade_log = []
            all_trades = []
            total = wins = 0
            date_groups = dict(iter(df.groupby("trade_date")))
            
            log(f"\n{'#'*80}")
            log(f"  策略: {name}")
            log(f"{'#'*80}")
            log(f"  总交易日: {len(dates)}")
            
            processed = 0
            i = 31
            while i < len(dates) - 1:
                t_date = dates[i]
                processed += 1
                if processed % 50 == 0:
                    log(f"  已处理 {processed}/{len(dates)} 天...")
                
                if t_date < pd.Timestamp("2025-05-01"):
                    i += 1
                    continue
                if t_date not in date_groups:
                    i += 1
                    continue
                
                day_df = date_groups[t_date].copy()
                cands = filter_fn(day_df)
                if cands.empty:
                    i += 1
                    continue
                
                n = len(cands)
                alloc = capital / n
                buyable = cands[cands["close"]*100 <= alloc].copy()
                if buyable.empty:
                    i += 1
                    continue
                if len(buyable) < n:
                    real_alloc = capital / len(buyable)
                    buyable = buyable[buyable["close"]*100 <= real_alloc]
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
                    shares = int(real_alloc / buy_price / 100) * 100
                    if shares <= 0:
                        continue
                    cost = shares * buy_price
                    
                    bars = []
                    for t_n in dates[i+1:i+4]:
                        if t_n not in date_groups:
                            continue
                        t_n_df = date_groups[t_n]
                        idx = t_n_df.set_index("ts_code")
                        if code not in idx.index:
                            continue
                        r = idx.loc[code]
                        if isinstance(r, pd.DataFrame):
                            r = r.iloc[0]
                        o,h,l,c = float(r["open"]),float(r["high"]),float(r["low"]),float(r["close"])
                        if any(pd.isna([o,h,l,c])) or o<=0 or h<=0 or l<=0 or c<=0:
                            continue
                        bars.append({"open":o,"high":h,"low":l,"close":c,"date":t_n})
                    
                    if not bars:
                        continue
                    
                    stop = buy_price * 0.98
                    target = buy_price * 1.05
                    sell_price = buy_price
                    reason = "无数据"
                    pnl = 0.0
                    
                    for idx2, bar in enumerate(bars):
                        bo,bh,bl,bc = bar["open"],bar["high"],bar["low"],bar["close"]
                        hold = idx2 + 1
                        is_last = (idx2 == len(bars)-1)
                        
                        if bo<=0 or bh<=0 or bl<=0 or bc<=0:
                            if is_last:
                                sell_price = bc if bc>0 else buy_price
                                pnl = sell_price/buy_price - 1
                                reason = f"数据缺失({pnl*100:+.2f}%)"
                            continue
                        
                        if bo <= stop:
                            sell_price = bo
                            pnl = bo/buy_price-1
                            reason = f"T+{hold}开盘止损-2%"
                            break
                        if bl <= stop:
                            sell_price = stop
                            pnl = stop/buy_price-1
                            reason = f"T+{hold}盘中止损-2%"
                            break
                        if bh >= target:
                            sell_price = target
                            pnl = target/buy_price-1
                            reason = f"T+{hold}盘中止盈+5%"
                            break
                        
                        if hold >= 3:
                            sell_price = bc
                            pnl = bc/buy_price-1
                            reason = f"持股{hold}天卖出({pnl*100:+.2f}%)"
                            break
                        
                        if is_last:
                            sell_price = bc
                            pnl = bc/buy_price-1
                            reason = f"持股{hold}日收盘({pnl*100:+.2f}%)"
                            break
                    
                    proceeds = shares * sell_price
                    pnl_amt = proceeds - cost
                    day_pnl += pnl_amt
                    total += 1
                    if pnl > 0:
                        wins += 1
                    
                    trade = {
                        "buy_date": t_date,
                        "sell_date": bars[0]["date"],
                        "code": code,
                        "strategy": row["strategy"],
                        "score": row["score"],
                        "buy_price": buy_price,
                        "sell_price": sell_price,
                        "shares": shares,
                        "cost": cost,
                        "proceeds": proceeds,
                        "pnl_amt": pnl_amt,
                        "pnl_pct": pnl,
                        "reason": reason
                    }
                    day_trades.append(trade)
                    all_trades.append(trade)
                
                if day_trades:
                    capital += day_pnl
                    trade_log.append({
                        "date": t_date,
                        "details": day_trades,
                        "day_pnl": day_pnl,
                        "capital_after": capital
                    })
                    log(f"  {pd.Timestamp(t_date).date()} | {len(day_trades)}只 | 盈亏{day_pnl:+,.2f} | 资金{capital:,.2f}")
                i += 1
            
            log(f"  ✓ 完成, 处理 {processed} 天")
            return trade_log, all_trades, capital, total, wins
        
        def filter_dip(day_df):
            mask = (
                (day_df["pct_chg"] >= -6.0) & (day_df["pct_chg"] <= -3.0) &
                (day_df["vol_ratio"] <= 0.8) & (day_df["vol_ratio"] > 0) &
                (day_df["ma20"].notna()) &
                (day_df["close"] >= day_df["ma20"] * 0.95) & (day_df["close"] <= day_df["ma20"] * 1.05) &
                (day_df["ma5"].notna()) & (day_df["close"] < day_df["ma5"] * 0.97) &
                (day_df["down_days"] >= 2) &
                (day_df["turnover_rate"] >= 3.0) & (day_df["turnover_rate"] <= 15.0) &
                (day_df["amount"] >= 30_000_000) & (day_df["close"] > 3.0)
            )
            c = day_df[mask].copy()
            if not c.empty:
                c["strategy"] = "dip"
                c["score"] = (
                    (c["pct_chg"].abs()/6.0)*30 +
                    (1-c["vol_ratio"]/0.8)*30 +
                    ((day_df["ma5"]-c["close"])/c["ma5"]*100)*20 +
                    (c["down_days"]/5.0)*20
                )
                c = c.sort_values("score", ascending=False).head(5)
            return c
        
        def filter_stabilize(day_df):
            mask = (
                (day_df["no_new_low_days"] >= 2) &
                (day_df["pct_chg"] >= 2.0) &
                (day_df["vol_ratio"] >= 1.5) &
                (day_df["ma5"].notna()) & (day_df["close"] > day_df["ma5"]) &
                (day_df["turnover_rate"] >= 3.0) & (day_df["turnover_rate"] <= 15.0) &
                (day_df["amount"] >= 30_000_000) & (day_df["close"] > 3.0)
            )
            c = day_df[mask].copy()
            if not c.empty:
                c["strategy"] = "stabilize"
                c["score"] = (
                    (c["pct_chg"]/10.0)*30 +
                    (c["vol_ratio"]/3.0)*30 +
                    (c["no_new_low_days"]/5.0)*20 +
                    ((c["close"]-c["ma5"])/c["ma5"]*100)*20
                )
                c = c.sort_values("score", ascending=False).head(5)
            return c
        
        def print_report(trade_log, all_trades, fc, total, wins, name):
            log(f"\n{'='*80}")
            log(f"  回测报告 — [{name}]")
            log(f"{'='*80}")
            log(f"  初始资金:     {1_000_000:>12,.2f} 元")
            log(f"  最终资金:     {fc:>12,.2f} 元")
            log(f"  总盈亏:       {fc-1_000_000:>+12,.2f} 元")
            log(f"  总收益率:     {(fc/1_000_000-1)*100:>+11.2f}%")
            log(f"  交易笔数:     {total:>12d}")
            log(f"  盈利笔数:     {wins:>12d}")
            log(f"  亏损笔数:     {total-wins:>12d}")
            log(f"  胜率:         {wins/max(total,1)*100:>11.1f}%")
            if total > 0:
                pnls = [t["pnl_pct"] for t in all_trades]
                log(f"  平均每笔:     {np.mean(pnls)*100:>+11.2f}%")
                log(f"  最大单笔盈利: {max(pnls)*100:>+8.2f}%")
                log(f"  最大单笔亏损: {min(pnls)*100:>+8.2f}%")
                mcl = ccl = 0
                for p in pnls:
                    if p < 0:
                        ccl += 1
                        mcl = max(mcl, ccl)
                    else:
                        ccl = 0
                log(f"  最大连续亏损: {mcl:>8d}笔")
                w = [p for p in pnls if p > 0]
                l = [p for p in pnls if p < 0]
                aw = np.mean(w)*100 if w else 0
                al = abs(np.mean(l))*100 if l else 0
                log(f"  平均盈利/平均亏损: {aw/al if al>0 else float('inf'):>10.2f}")
            
            log(f"\n{'─'*80}")
            log(f"  逐笔交易明细:")
            log(f"    {'序号':>4} {'买入日':<12} {'卖出日':<12} {'代码':<12} {'策略':<10} {'买价':>8} {'卖价':>8} {'股数':>8} {'成本':>12} {'盈亏%':>8} {'盈亏元':>12} {'原因':<20}")
            log(f"  {'─'*4} {'─'*12} {'─'*12} {'─'*12} {'─'*10} {'─'*8} {'─'*8} {'─'*8} {'─'*12} {'─'*8} {'─'*12} {'─'*20}")
            for idx, t in enumerate(all_trades, 1):
                log(f"    {idx:>4} {pd.Timestamp(t['buy_date']).date():<12} {pd.Timestamp(t['sell_date']).date():<12} {t['code']:<12} {t['strategy']:<10} {t['buy_price']:>8.2f} {t['sell_price']:>8.2f} {t['shares']:>8} {t['cost']:>12,.2f} {t['pnl_pct']*100:>+7.2f}% {t['pnl_amt']:>+12,.2f} {t['reason']:<20}")
            
            log(f"\n{'─'*80}")
            log(f"  逐日汇总:")
            log(f"  {'买入日':<12} {'交易数':>6} {'当日盈亏':>14} {'资金余额':>14} {'累计收益率':>12}")
            log(f"  {'─'*12} {'─'*6} {'─'*14} {'─'*14} {'─'*12}")
            for e in trade_log:
                cum = (e["capital_after"]/1_000_000-1)*100
                log(f"  {pd.Timestamp(e['date']).date():<12} {len(e['details']):>6} {e['day_pnl']:>+14,.2f} {e['capital_after']:>14,.2f} {cum:>+11.2f}%")
            log(f"\n{'='*80}")
        
        tl1, at1, fc1, t1, w1 = run_bt("低吸策略", filter_dip)
        print_report(tl1, at1, fc1, t1, w1, "低吸策略")
        
        tl2, at2, fc2, t2, w2 = run_bt("右侧企稳策略", filter_stabilize)
        print_report(tl2, at2, fc2, t2, w2, "右侧企稳策略")
        
        log(f"\n{'='*80}")
        log(f"  策略对比")
        log(f"{'='*80}")
        log(f"  {'指标':<20} {'低吸策略':>15} {'右侧企稳':>15}")
        log(f"  {'最终资金':<20} {fc1:>15,.2f} {fc2:>15,.2f}")
        log(f"  {'总收益率':<20} {(fc1/1e6-1)*100:>+14.2f}% {(fc2/1e6-1)*100:>+14.2f}%")
        log(f"  {'交易笔数':<20} {t1:>15d} {t2:>15d}")
        log(f"  {'胜率':<20} {w1/max(t1,1)*100:>+14.1f}% {w2/max(t2,1)*100:>+14.1f}%")
        log(f"\n结果已保存到: {OUTPUT_FILE}")
        _out.close()
    except Exception as e:
        log(f"\n✗ 错误: {e}")
        import traceback
        log(traceback.format_exc())
        if _out:
            _out.close()

if __name__ == "__main__":
    main()
