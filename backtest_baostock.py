"""
使用baostock获取A股数据进行回测 - 右侧企稳策略
================================================
回测区间: 2025-05-01 ~ 2026-05-25
数据来源: baostock (离线数据源)
资金管理: 固定金额5万元/笔，最多持仓3只
止损: -2%
止盈: T+1日5%，T+2及以后10%
最大持股: 5天
"""
import baostock as bs
import pandas as pd
import numpy as np
import time
import os
from datetime import datetime, timedelta

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

CACHE_PATH = r'd:\pythonProject\openclaw-quant-system\baostock_data_cache.parquet'
OUTPUT_FILE = r'd:\pythonProject\openclaw-quant-system\backtest_baostock_result.txt'

_out = None

def log(msg="", end="\n"):
    global _out
    # 替换特殊字符为ASCII兼容字符
    msg = msg.replace("✓", "[OK]").replace("✗", "[ERR]").replace("─", "-")
    print(msg, end=end, flush=True)
    if _out:
        _out.write(msg + end)
        _out.flush()


def get_stock_codes():
    """获取A股股票代码列表"""
    log("步骤1: 准备A股股票列表...")
    # 使用常见的A股代码列表（沪深两市主要股票）
    codes = [
        # 上证A股
        "sh.600000", "sh.600004", "sh.600006", "sh.600007", "sh.600008", "sh.600009", "sh.600010", "sh.600011",
        "sh.600012", "sh.600015", "sh.600016", "sh.600017", "sh.600018", "sh.600019", "sh.600020", "sh.600021",
        "sh.600022", "sh.600023", "sh.600025", "sh.600026", "sh.600027", "sh.600028", "sh.600029", "sh.600030",
        "sh.600031", "sh.600032", "sh.600033", "sh.600035", "sh.600036", "sh.600037", "sh.600038", "sh.600039",
        "sh.600048", "sh.600050", "sh.600051", "sh.600052", "sh.600053", "sh.600054", "sh.600055", "sh.600056",
        "sh.600057", "sh.600058", "sh.600059", "sh.600060", "sh.600061", "sh.600062", "sh.600063", "sh.600064",
        "sh.600066", "sh.600067", "sh.600071", "sh.600072", "sh.600073", "sh.600075", "sh.600076", "sh.600078",
        "sh.600079", "sh.600080", "sh.600081", "sh.600082", "sh.600084", "sh.600085", "sh.600088", "sh.600089",
        "sh.600094", "sh.600095", "sh.600096", "sh.600097", "sh.600098", "sh.600099", "sh.600100", "sh.600101",
        "sh.600103", "sh.600104", "sh.600105", "sh.600106", "sh.600107", "sh.600108", "sh.600109", "sh.600110",
        # 深证A股
        "sz.000001", "sz.000002", "sz.000004", "sz.000005", "sz.000006", "sz.000007", "sz.000008", "sz.000009",
        "sz.000010", "sz.000011", "sz.000012", "sz.000014", "sz.000016", "sz.000017", "sz.000019", "sz.000020",
        "sz.000021", "sz.000025", "sz.000026", "sz.000027", "sz.000028", "sz.000029", "sz.000030", "sz.000031",
        "sz.000032", "sz.000034", "sz.000035", "sz.000036", "sz.000037", "sz.000039", "sz.000042", "sz.000045",
        "sz.000048", "sz.000049", "sz.000050", "sz.000055", "sz.000056", "sz.000058", "sz.000059", "sz.000060",
        "sz.000061", "sz.000062", "sz.000063", "sz.000065", "sz.000066", "sz.000068", "sz.000070", "sz.000078",
        "sz.000088", "sz.000089", "sz.000090", "sz.000096", "sz.000099", "sz.000100", "sz.000151", "sz.000153",
        "sz.000155", "sz.000156", "sz.000157", "sz.000158", "sz.000159", "sz.000166", "sz.000301", "sz.000333",
        "sz.000338", "sz.000400", "sz.000401", "sz.000402", "sz.000403", "sz.000404", "sz.000410", "sz.000411",
        "sz.000415", "sz.000417", "sz.000419", "sz.000420", "sz.000421", "sz.000422", "sz.000423", "sz.000425",
        "sz.000426", "sz.000429", "sz.000430", "sz.000488", "sz.000498", "sz.000501", "sz.000503", "sz.000504",
        "sz.000505", "sz.000506", "sz.000507", "sz.000509", "sz.000510", "sz.000513", "sz.000514", "sz.000516",
        "sz.000517", "sz.000518", "sz.000519", "sz.000520", "sz.000521", "sz.000523", "sz.000524", "sz.000525",
        "sz.000526", "sz.000528", "sz.000530", "sz.000532", "sz.000533", "sz.000534", "sz.000536", "sz.000537",
        "sz.000538", "sz.000539", "sz.000540", "sz.000541", "sz.000543", "sz.000544", "sz.000545", "sz.000546",
        "sz.000547", "sz.000548", "sz.000550", "sz.000551", "sz.000552", "sz.000553", "sz.000554", "sz.000555",
        "sz.000557", "sz.000558", "sz.000559", "sz.000560", "sz.000561", "sz.000563", "sz.000564", "sz.000565",
        "sz.000566", "sz.000567", "sz.000568", "sz.000570", "sz.000571", "sz.000572", "sz.000573", "sz.000576",
        "sz.000578", "sz.000581", "sz.000582", "sz.000586", "sz.000589", "sz.000590", "sz.000591", "sz.000592",
        "sz.000593", "sz.000595", "sz.000596", "sz.000597", "sz.000598", "sz.000599", "sz.000600", "sz.000601",
        "sz.000603", "sz.000605", "sz.000607", "sz.000608", "sz.000609", "sz.000610", "sz.000612", "sz.000615",
        "sz.000617", "sz.000619", "sz.000620", "sz.000621", "sz.000623", "sz.000625", "sz.000626", "sz.000627",
        "sz.000628", "sz.000629", "sz.000630", "sz.000631", "sz.000632", "sz.000633", "sz.000635", "sz.000636",
        "sz.000637", "sz.000638", "sz.000639", "sz.000650", "sz.000651", "sz.000652", "sz.000653", "sz.000655",
        "sz.000656", "sz.000657", "sz.000659", "sz.000661", "sz.000663", "sz.000665", "sz.000666", "sz.000667",
        "sz.000668", "sz.000669", "sz.000670", "sz.000671", "sz.000672", "sz.000673", "sz.000676", "sz.000677",
        "sz.000678", "sz.000679", "sz.000680", "sz.000681", "sz.000682", "sz.000683", "sz.000685", "sz.000686",
        "sz.000687", "sz.000688", "sz.000690", "sz.000691", "sz.000692", "sz.000693", "sz.000695", "sz.000696",
        "sz.000697", "sz.000698", "sz.000699", "sz.000700", "sz.000701", "sz.000702", "sz.000703", "sz.000705",
        "sz.000707", "sz.000708", "sz.000709", "sz.000710", "sz.000711", "sz.000712", "sz.000713", "sz.000715",
        "sz.000716", "sz.000717", "sz.000718", "sz.000719", "sz.000720", "sz.000721", "sz.000722", "sz.000723",
        "sz.000725", "sz.000726", "sz.000727", "sz.000728", "sz.000729", "sz.000730", "sz.000731", "sz.000732",
        "sz.000733", "sz.000735", "sz.000736", "sz.000737", "sz.000738", "sz.000739", "sz.000750", "sz.000751",
        "sz.000752", "sz.000753", "sz.000755", "sz.000756", "sz.000757", "sz.000758", "sz.000759", "sz.000761",
        "sz.000762", "sz.000766", "sz.000767", "sz.000768", "sz.000776", "sz.000777", "sz.000778", "sz.000779",
        "sz.000780", "sz.000782", "sz.000783", "sz.000785", "sz.000786", "sz.000788", "sz.000789", "sz.000790",
        "sz.000791", "sz.000792", "sz.000793", "sz.000795", "sz.000796", "sz.000797", "sz.000798", "sz.000799",
        "sz.000800", "sz.000801", "sz.000802", "sz.000803", "sz.000807", "sz.000809", "sz.000810", "sz.000811",
        "sz.000812", "sz.000813", "sz.000815", "sz.000816", "sz.000818", "sz.000819", "sz.000820", "sz.000821",
        "sz.000822", "sz.000823", "sz.000825", "sz.000826", "sz.000828", "sz.000829", "sz.000830", "sz.000831",
        "sz.000833", "sz.000835", "sz.000836", "sz.000837", "sz.000838", "sz.000839", "sz.000848", "sz.000850",
        "sz.000851", "sz.000852", "sz.000856", "sz.000858", "sz.000859", "sz.000860", "sz.000861", "sz.000862",
        "sz.000863", "sz.000866", "sz.000868", "sz.000869", "sz.000875", "sz.000876", "sz.000877", "sz.000878",
        "sz.000880", "sz.000881", "sz.000882", "sz.000883", "sz.000885", "sz.000886", "sz.000887", "sz.000888",
        "sz.000889", "sz.000890", "sz.000892", "sz.000893", "sz.000895", "sz.000897", "sz.000898", "sz.000899",
        "sz.000900", "sz.000901", "sz.000902", "sz.000903", "sz.000905", "sz.000906", "sz.000908", "sz.000909",
        "sz.000910", "sz.000911", "sz.000912", "sz.000913", "sz.000915", "sz.000916", "sz.000917", "sz.000918",
        "sz.000919", "sz.000920", "sz.000921", "sz.000922", "sz.000923", "sz.000925", "sz.000926", "sz.000927",
        "sz.000928", "sz.000929", "sz.000930", "sz.000931", "sz.000932", "sz.000933", "sz.000935", "sz.000936",
        "sz.000937", "sz.000938", "sz.000939", "sz.000948", "sz.000949", "sz.000950", "sz.000951", "sz.000952",
        "sz.000953", "sz.000955", "sz.000956", "sz.000957", "sz.000958", "sz.000959", "sz.000960", "sz.000961",
        "sz.000962", "sz.000963", "sz.000965", "sz.000966", "sz.000967", "sz.000968", "sz.000969", "sz.000970",
        "sz.000971", "sz.000972", "sz.000973", "sz.000975", "sz.000976", "sz.000977", "sz.000978", "sz.000980",
        "sz.000981", "sz.000982", "sz.000983", "sz.000985", "sz.000987", "sz.000988", "sz.000989", "sz.000990",
        "sz.000993", "sz.000995", "sz.000996", "sz.000997", "sz.000998", "sz.000999",
    ]
    log(f"  ✓ 使用 {len(codes)} 只股票")
    return codes


def fetch_stock_data(codes, start_date, end_date):
    """批量获取股票日线数据"""
    log(f"\n步骤2: 获取 {len(codes)} 只股票的日线数据...")
    t0 = time.time()
    
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    lookback_start = (start_dt - timedelta(days=60)).strftime("%Y-%m-%d")
    
    all_dfs = []
    success = 0
    fail = 0
    total = len(codes)
    
    for i, code in enumerate(codes):
        retry = 0
        max_retry = 3
        while retry < max_retry:
            try:
                rs = bs.query_history_k_data_plus(
                    code,
                    "date,open,high,low,close,volume,amount,turn,pctChg",
                    start_date=lookback_start,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="2"
                )
                
                if rs.error_code == '0':
                    data_list = []
                    while (rs.error_code == '0') & rs.next():
                        data_list.append(rs.get_row_data())
                    
                    if data_list:
                        df = pd.DataFrame(data_list, columns=rs.fields)
                        df['ts_code'] = code
                        all_dfs.append(df)
                        success += 1
                        break  # 成功，跳出重试
                    else:
                        fail += 1
                        break
                else:
                    retry += 1
                    time.sleep(1)
                    continue
                    
            except Exception as e:
                retry += 1
                time.sleep(1)
                continue
        
        if retry >= max_retry:
            fail += 1
        
        # 每10只输出一次进度
        if (i + 1) % 10 == 0 or i + 1 == total:
            elapsed = time.time() - t0
            log(f"  进度: {i+1}/{total}, 成功{success}, 失败{fail}, 耗时{elapsed:.0f}s")
        
        # 每获取50只股票后重新登录，避免会话过期
        if (i + 1) % 50 == 0:
            bs.logout()
            time.sleep(1)
            lg = bs.login()
            if lg.error_code == '0':
                log(f"  重新登录成功")
            else:
                log(f"  重新登录失败: {lg.error_msg}")
    
    if not all_dfs:
        raise Exception("没有成功获取任何数据")
    
    df = pd.concat(all_dfs, ignore_index=True)
    
    # 重命名列
    df = df.rename(columns={
        'date': 'trade_date',
        'open': 'open',
        'high': 'high',
        'low': 'low',
        'close': 'close',
        'volume': 'volume',
        'amount': 'amount',
        'turn': 'turnover_rate',
        'pctChg': 'pct_chg'
    })
    
    # 数据类型转换
    for col in ['open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg', 'turnover_rate']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.dropna(subset=['close', 'pct_chg'])
    df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    
    log(f"\n  ✓ 总计: {len(df)} 条, {df['ts_code'].nunique()} 只股票")
    log(f"  成功: {success}, 失败: {fail}")
    
    return df


def compute_indicators(df):
    """计算技术指标"""
    log("\n步骤3: 计算技术指标...")
    t0 = time.time()
    
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
    
    def calc_no_new_low(group):
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
    log("回测 — 右侧企稳策略 (baostock数据)")
    log(f"回测区间: {START_DATE} ~ {END_DATE}")
    log(f"初始资金: 100万元 | 止损-2% | 最大持股{MAX_HOLD}天")
    log("="*80)
    
    start_dt = pd.Timestamp(START_DATE)
    end_dt = pd.Timestamp(END_DATE)
    df_bt = df[(df["trade_date"] >= start_dt) & (df["trade_date"] <= end_dt)].copy()
    
    trading_dates = sorted(df_bt["trade_date"].unique())
    log(f"\n总交易日: {len(trading_dates)}")
    
    df_indexed = df_bt.set_index(["ts_code", "trade_date"])
    
    capital = INITIAL_CAPITAL
    positions = []
    trades = []
    
    for t_date in trading_dates:
        day_df = df_bt[df_bt["trade_date"] == t_date]
        
        new_positions = []
        sell_count = 0
        sell_pnl = 0
        
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
        
        if buy_count > 0 or sell_count > 0:
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
    
    log(f"\n  按策略统计:")
    strategies = set(t["strategy"] for t in trades)
    for strat in strategies:
        strat_trades = [t for t in trades if t["strategy"] == strat]
        strat_wins = len([t for t in strat_trades if t["pnl"] > 0])
        log(f"    {strat}:   {len(strat_trades)} 笔, 胜率 {strat_wins/len(strat_trades)*100 if strat_trades else 0:.1f}%")
    
    log(f"\n{'='*120}")
    log(f"逐笔交易明细:")
    log(f"{'='*120}")
    log(f"{'序号':>4} {'买入日':<12} {'卖出日':<12} {'代码':<14} {'策略':<8} {'买价':>8} {'卖价':>8} {'盈亏%':>8} {'盈亏元':>10} {'原因':<20}")
    log(f"{'─'*4} {'─'*12} {'─'*12} {'─'*14} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*10} {'─'*20}")
    
    for i, t in enumerate(trades, 1):
        log(f"{i:>4} {t['buy_date']:<12} {t['sell_date']:<12} {t['code']:<14} {t['strategy']:<8} {t['buy_price']:>8.2f} {t['sell_price']:>8.2f} {t['pnl_pct']:>+7.2f}% {t['pnl']:>+10,.0f} {t['reason']:<20}")
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("="*80 + "\n")
        f.write("回测报告 — baostock数据\n")
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
    
    lg = bs.login()
    if lg.error_code != '0':
        log(f"baostock登录失败: {lg.error_msg}")
        return
    
    log("baostock登录成功")
    
    try:
        # 先尝试加载缓存
        if os.path.exists(CACHE_PATH):
            log(f"从缓存加载数据: {CACHE_PATH}")
            df = pd.read_parquet(CACHE_PATH)
            log(f"  ✓ 加载 {len(df)} 条, {df['ts_code'].nunique()} 只股票")
        else:
            # 获取股票列表
            codes = get_stock_codes()
            if not codes:
                log("无法获取股票列表，退出")
                return
            
            log(f"准备获取 {len(codes)} 只股票数据...")
            
            # 先测试前50只
            codes = codes[:50]
            log(f"使用前 {len(codes)} 只股票进行测试...")
            
            df = fetch_stock_data(codes, START_DATE, END_DATE)
            
            # 保存缓存
            df.to_parquet(CACHE_PATH, index=False)
            log(f"数据已保存到缓存: {CACHE_PATH}")
        
        # 计算指标
        df = compute_indicators(df)
        
        # 运行回测
        run_backtest(df)
        
    finally:
        bs.logout()
        if _out:
            _out.close()


if __name__ == "__main__":
    main()
