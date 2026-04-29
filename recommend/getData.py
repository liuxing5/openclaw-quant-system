import baostock as bs
import pandas as pd
from datetime import datetime, timedelta
import time
import os

import pickle
import hashlib

CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_EXPIRE_DAYS = 1   # 缓存有效期（交易日），可设为 0 表示永远不过期


FIELDS = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"
SAVE_FILE = "last_results.csv"


# ====================== 日期 ======================
def get_last_trading_date(days_back=1):
    today = datetime.now().date()
    for i in range(90):
        check_date = (today - timedelta(days=i + days_back)).strftime('%Y-%m-%d')
        if datetime.strptime(check_date, '%Y-%m-%d').weekday() >= 5:
            continue
        return check_date
    return (today - timedelta(days=1)).strftime('%Y-%m-%d')


# ====================== 数据 ======================
def get_yesterday_data(code):
    end_date = get_last_trading_date(days_back=0)
    start_date = (datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=10)).strftime('%Y-%m-%d')

    # ================== 缓存逻辑 ==================
    cache_key = f"{code}_{start_date}_{end_date}"
    cache_file = os.path.join(CACHE_DIR, f"{hashlib.md5(cache_key.encode()).hexdigest()}.pkl")

    # 检查缓存是否存在且未过期
    if os.path.exists(cache_file):
        file_mtime = datetime.fromtimestamp(os.path.getmtime(cache_file))
        if (datetime.now() - file_mtime).days < CACHE_EXPIRE_DAYS:
            try:
                with open(cache_file, 'rb') as f:
                    df = pickle.load(f)
                print(f"   📂 从缓存读取: {code}")
                return df
            except:
                pass  # 缓存损坏则重新获取

    # ================== 无缓存或过期 → 从 baostock 获取 ==================
    print(f"   📡 从 baostock 获取: {code}")

    for attempt in range(3):
        rs = bs.query_history_k_data_plus(
            code, FIELDS,
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="3"
        )

        data_list = []
        while rs.error_code == '0' and rs.next():
            data_list.append(rs.get_row_data())

        if data_list:
            df = pd.DataFrame(data_list, columns=rs.fields)
            df['date'] = pd.to_datetime(df['date'])

            numeric_cols = ['open', 'high', 'low', 'close', 'preclose', 'volume', 'turn', 'pctChg']
            for col in numeric_cols:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            df = df.sort_values('date').dropna(subset=numeric_cols)

            # 保存到缓存
            with open(cache_file, 'wb') as f:
                pickle.dump(df, f)

            print(f"   ✅ 数据OK，共{len(df)}条（已缓存）")
            return df

        print(f"   ⚠️ 重试 {attempt+1}")

    print(f"   ❌ 获取失败")
    return None


# ====================== 工具 ======================
def calc_strength(row):
    high = float(row['high'])
    low = float(row['low'])
    close = float(row['close'])
    return (close - low) / (high - low + 1e-6)


def get_limit_up_threshold(code):
    return 20.0 if code.startswith(('sh.688','sz.300')) else 10.0


# ====================== 分析 ======================
def analyze_stock(code, results, edge_list):
    print(f"\n🔍 分析 {code}")

    df = get_yesterday_data(code)
    if df is None or len(df) < 5:
        print("   ❌ 数据不足")
        return

    latest = df.iloc[-1]

    try:
        pct = float(latest['pctChg'])
        turn = float(latest['turn']) if latest['turn'] else 0
        strength = calc_strength(latest)

        high = latest['high']
        low = latest['low']
        close = latest['close']
        volume = latest['volume']

        # ===== 技术指标 =====
        amplitude = (high - low) / low * 100 if low > 0 else 0
        close_pos = (close - low) / (high - low + 1e-6)

        avg_vol = df['volume'].tail(5).mean()
        vol_ratio = volume / avg_vol if avg_vol > 0 else 0

        ma5 = df['close'].tail(5).mean()
        ma10 = df['close'].tail(10).mean() if len(df) >= 10 else ma5
        trend_up = close > ma5 > ma10

        threshold = get_limit_up_threshold(code)
        limit_price = latest['preclose'] * (1 + threshold / 100)
        is_broken = high >= limit_price and close < limit_price

        # ===== 打印 =====
        print(f"   📊 涨幅: {pct:.2f}%")
        print(f"   📊 换手: {turn:.2f}%")
        print(f"   📊 强度: {strength:.2f}")
        print(f"   📊 振幅: {amplitude:.2f}%")
        print(f"   📊 收盘位置: {close_pos:.2f}")
        print(f"   📊 量比: {vol_ratio:.2f}")
        print(f"   📊 趋势: {'上涨' if trend_up else '震荡'}")
        print(f"   📊 状态: {'炸板⚠️' if is_broken else '正常'}")

        # ===== 标签 =====
        tag = ""
        if strength > 0.8 and vol_ratio > 1.5:
            tag = "🔥 强封放量"
        elif is_broken:
            tag = "💣 炸板风险"
        elif strength < 0.6:
            tag = "⚠️ 弱封"
        else:
            tag = "📊 普通"

        print(f"   🏷️ 标签: {tag}")

        # ===== 推荐逻辑 =====
        if pct >= threshold - 0.3 and strength >= 0.75 and turn >= 3:
            score = strength * 10 + turn + vol_ratio
            results.append((code, close, score, strength, turn, vol_ratio, tag))
            print(f"   🎯 入选推荐 | 分数 {score:.2f}")
            return

        # ===== 边缘逻辑 =====
        edge_score = 0

        if pct > threshold - 1:
            edge_score = pct / threshold
            reason = "涨停边缘"
        elif strength > 0.7:
            edge_score = strength / 0.75
            reason = "强度边缘"
        elif turn > 2:
            edge_score = turn / 3
            reason = "换手边缘"
        else:
            print("   ❌ 未通过")
            return

        edge_list.append((code, close, reason, edge_score))
        print(f"   ⚠️ 边缘: {reason} | 接近度 {edge_score:.2f}")

    except Exception as e:
        print(f"   ❌ 异常: {e}")


# ====================== 次日验证 ======================
def evaluate_today():
    if not os.path.exists(SAVE_FILE):
        print("📭 无历史数据")
        return

    print("\n========== 📊 胜率统计 ==========")

    df_old = pd.read_csv(SAVE_FILE)

    win = 0
    total = 0

    for _, row in df_old.iterrows():
        code = row['code']
        old_close = row['close']

        df = get_yesterday_data(code)
        if df is None:
            continue

        new_close = float(df.iloc[-1]['close'])
        change = (new_close - old_close) / old_close * 100

        print(f"{code} | {change:.2f}%")

        if change > 0:
            win += 1
        total += 1

    if total > 0:
        print(f"\n🎯 胜率: {win}/{total} = {win/total:.2%}")


# ====================== 主程序 ======================
print("🔥 系统启动")

bs.login()
print("✅ 登录成功")

evaluate_today()

print("\n📦 获取股票池...")

stocks = set()

for func in [bs.query_hs300_stocks, bs.query_sz50_stocks, bs.query_zz500_stocks]:
    rs = func()
    while rs.next():
        stocks.add(rs.get_row_data()[1])

stocks = list(stocks)
print(f"✅ 股票数量: {len(stocks)}")

results = []
edge_list = []

print("\n========== 开始分析 ==========")

for i, code in enumerate(stocks[:50], 1):
    print(f"\n🚀 进度 {i}/{len(stocks)}")
    analyze_stock(code, results, edge_list)
    time.sleep(0.2)

bs.logout()
print("✅ 登出完成")


# ====================== 输出 ======================
print("\n========== 🎯 今日推荐 ==========")
for r in sorted(results, key=lambda x: x[2], reverse=True)[:10]:
    print(f"{r[0]} | 分:{r[2]:.2f} | 强:{r[3]:.2f} | 换:{r[4]:.1f}% | 量:{r[5]:.2f} | {r[6]}")

print("\n========== ⚠️ 边缘机会 ==========")
for e in sorted(edge_list, key=lambda x: x[3], reverse=True)[:10]:
    print(f"{e[0]} | {e[2]} | 接近:{e[3]:.2f}")


# ====================== 保存 ======================
save_data = []

for r in results[:10]:
    save_data.append([r[0], r[1]])

for e in edge_list[:10]:
    save_data.append([e[0], e[1]])

pd.DataFrame(save_data, columns=["code", "close"]).to_csv(SAVE_FILE, index=False)

print("\n💾 已保存（用于明日胜率统计）")
print("🎉 程序结束")