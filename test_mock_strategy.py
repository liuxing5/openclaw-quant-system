"""
使用模拟数据测试双策略逻辑
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import random

# 生成模拟数据
np.random.seed(42)
random.seed(42)

START_DATE = "2025-04-01"
END_DATE = "2026-05-25"
NUM_STOCKS = 100  # 模拟100只股票

def generate_mock_data():
    """生成模拟股票数据"""
    dates = pd.bdate_range(START_DATE, END_DATE)
    # 过滤掉非交易日（简化处理）
    dates = dates[dates.dayofweek < 5]  # 只保留工作日
    
    all_data = []
    
    for i in range(NUM_STOCKS):
        ts_code = f"{'000001' if i < 50 else '600001'}.{i:03d}"
        base_price = random.uniform(5, 100)
        
        price = base_price
        for date in dates:
            # 模拟价格变动
            pct = np.random.normal(0, 0.02)  # 日均波动2%
            price = price * (1 + pct)
            
            # 模拟开盘价、最高价、最低价
            open_price = price * (1 + np.random.normal(0, 0.005))
            high_price = max(open_price, price) * (1 + abs(np.random.normal(0, 0.01)))
            low_price = min(open_price, price) * (1 - abs(np.random.normal(0, 0.01)))
            
            # 模拟成交量和成交额
            volume = random.randint(100000, 10000000)
            amount = volume * price
            turnover_rate = random.uniform(0.5, 20)
            
            all_data.append({
                "ts_code": ts_code,
                "trade_date": date,
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(price, 2),
                "volume": volume,
                "amount": round(amount, 2),
                "pct_chg": round(pct * 100, 2),
                "turnover_rate": round(turnover_rate, 2)
            })
    
    df = pd.DataFrame(all_data)
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return df

print("生成模拟数据...")
df = generate_mock_data()
print(f"✓ 生成 {len(df)} 条数据, {df['ts_code'].nunique()} 只股票")
print(f"  日期: {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}")

# 保存模拟数据
import pickle
CACHE_FILE = r'd:\pythonProject\openclaw-quant-system\mock_data.pkl'
with open(CACHE_FILE, "wb") as f:
    pickle.dump(df, f)
print(f"✓ 模拟数据已保存到 {CACHE_FILE}")

# 计算指标
print("\n计算指标...")
df["ma5"] = df.groupby("ts_code")["close"].transform(
    lambda x: x.rolling(5, min_periods=5).mean().shift(1)
)
df["ma20"] = df.groupby("ts_code")["close"].transform(
    lambda x: x.rolling(20, min_periods=20).mean().shift(1)
)

# 计算量比
avg_vol = df.groupby("ts_code")["volume"].transform(
    lambda x: x.rolling(10, min_periods=5).mean().shift(1)
)
df["vol_ratio"] = df["volume"] / avg_vol

# 计算连续下跌天数
def calc_down_days(pct_series):
    down_days = [0] * len(pct_series)
    for i in range(len(pct_series)):
        if pct_series.iloc[i] < 0:
            down_days[i] = down_days[i - 1] + 1 if i > 0 else 1
        else:
            down_days[i] = 0
    return pd.Series(down_days, index=pct_series.index)

df["down_days"] = df.groupby("ts_code")["pct_chg"].transform(calc_down_days)

# 计算连续不创新低天数
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

# 测试低吸策略
print("\n测试低吸策略筛选...")
DIP_PCT_LO = -6.0
DIP_PCT_HI = -3.0
DIP_VOL_RATIO_MAX = 0.8
DIP_MA20_TOLERANCE = 0.05
DIP_MA5_DEVIATION = 0.03
DIP_TURN_MIN = 3.0
DIP_TURN_MAX = 15.0
DIP_MIN_AMOUNT = 30_000_000

# 只测试2025-05-01之后的数据
test_start = pd.Timestamp("2025-05-01")
test_df = df[df["trade_date"] >= test_start].copy()

dip_mask = (
    (test_df["pct_chg"] >= DIP_PCT_LO) &
    (test_df["pct_chg"] <= DIP_PCT_HI) &
    (test_df["vol_ratio"] <= DIP_VOL_RATIO_MAX) &
    (test_df["vol_ratio"] > 0) &
    (test_df["ma20"].notna()) &
    (test_df["close"] >= test_df["ma20"] * (1 - DIP_MA20_TOLERANCE)) &
    (test_df["close"] <= test_df["ma20"] * (1 + DIP_MA20_TOLERANCE)) &
    (test_df["ma5"].notna()) &
    (test_df["close"] < test_df["ma5"] * (1 - DIP_MA5_DEVIATION)) &
    (test_df["down_days"] >= 2) &
    (test_df["turnover_rate"] >= DIP_TURN_MIN) &
    (test_df["turnover_rate"] <= DIP_TURN_MAX) &
    (test_df["amount"] >= DIP_MIN_AMOUNT) &
    (test_df["close"] > 3.0)
)

dip_candidates = test_df[dip_mask]
print(f"  低吸候选: {len(dip_candidates)} 只")

# 测试右侧企稳策略
print("\n测试右侧企稳策略筛选...")
STABILIZE_PCT_MIN = 2.0
STABILIZE_VOL_RATIO_MIN = 1.5
STABILIZE_TURN_MIN = 3.0
STABILIZE_TURN_MAX = 15.0
STABILIZE_MIN_AMOUNT = 30_000_000

stabilize_mask = (
    (test_df["no_new_low_days"] >= 2) &
    (test_df["pct_chg"] >= STABILIZE_PCT_MIN) &
    (test_df["vol_ratio"] >= STABILIZE_VOL_RATIO_MIN) &
    (test_df["ma5"].notna()) &
    (test_df["close"] > test_df["ma5"]) &
    (test_df["turnover_rate"] >= STABILIZE_TURN_MIN) &
    (test_df["turnover_rate"] <= STABILIZE_TURN_MAX) &
    (test_df["amount"] >= STABILIZE_MIN_AMOUNT) &
    (test_df["close"] > 3.0)
)

stabilize_candidates = test_df[stabilize_mask]
print(f"  右侧企稳候选: {len(stabilize_candidates)} 只")

print("\n✓ 策略逻辑测试完成!")
print(f"\n总结:")
print(f"  模拟数据: {len(df)} 条, {df['ts_code'].nunique()} 只股票")
print(f"  测试区间: {test_start.date()} ~ {df['trade_date'].max().date()}")
print(f"  低吸候选: {len(dip_candidates)} 只")
print(f"  右侧企稳候选: {len(stabilize_candidates)} 只")
