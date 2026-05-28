"""查000026在4/10-4/15的技术指标"""
import sys, os
sys.path.insert(0, 'd:/pythonProject/openclaw-quant-system')
os.environ['POSTGRES_HOST'] = 'aws-1-ap-northeast-1.pooler.supabase.com'
os.environ['POSTGRES_PORT'] = '5432'
os.environ['POSTGRES_USER'] = 'postgres.qoakbxswwjqfsgbcgepr'
os.environ['POSTGRES_PASSWORD'] = 'wYFBB91zViSrk2vl'
os.environ['POSTGRES_DB'] = 'postgres'
os.environ['POSTGRES_SSLMODE'] = 'require'

from strategies.meta_strategy.db_data_adapter import get_daily_quotes
from datetime import date
import pandas as pd
import numpy as np

# 获取000026的K线数据
df = get_daily_quotes('000026.SZ', date(2026, 3, 1), date(2026, 4, 15))
if df.empty:
    print("无数据")
    sys.exit()

df['trade_date'] = pd.to_datetime(df['trade_date'])
df = df.sort_values('trade_date').reset_index(drop=True)

# 计算技术指标
closes = df['close'].values

# RSI
def calc_rsi(prices, period=14):
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.zeros_like(prices)
    avg_loss = np.zeros_like(prices)
    avg_gain[period] = np.mean(gains[:period])
    avg_loss[period] = np.mean(losses[:period])
    for i in range(period+1, len(prices)):
        avg_gain[i] = (avg_gain[i-1] * (period-1) + gains[i-1]) / period
        avg_loss[i] = (avg_loss[i-1] * (period-1) + losses[i-1]) / period
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100)
    rsi = 100 - 100 / (1 + rs)
    return rsi

rsi = calc_rsi(closes)

# MACD
def calc_ema(prices, period):
    ema = np.zeros_like(prices)
    ema[0] = prices[0]
    k = 2 / (period + 1)
    for i in range(1, len(prices)):
        ema[i] = prices[i] * k + ema[i-1] * (1-k)
    return ema

ema12 = calc_ema(closes, 12)
ema26 = calc_ema(closes, 26)
dif = ema12 - ema26
dea = calc_ema(dif, 9)

# SAR (简化)
# momentum
momentum = np.zeros_like(closes)
for i in range(20, len(closes)):
    momentum[i] = (closes[i] - closes[i-20]) / closes[i-20]

# 打印4/7-4/15的指标
print("000026.SZ 技术指标:")
print(f"{'日期':12s} {'close':>7s} {'pct':>7s} {'RSI':>6s} {'DIF':>7s} {'DEA':>7s} {'MACD多':>6s} {'momentum':>9s}")
for i in range(len(df)):
    d = str(df.iloc[i]['trade_date'])[:10]
    if d >= '2026-04-07' and d <= '2026-04-15':
        pct = float(df.iloc[i].get('pct_chg', 0))
        macd_bull = 'Y' if dif[i] > dea[i] else 'N'
        print(f"{d:12s} {closes[i]:7.2f} {pct:+6.2f}% {rsi[i]:6.1f} {dif[i]:7.3f} {dea[i]:7.3f} {macd_bull:>6s} {momentum[i]:+8.4f}")
