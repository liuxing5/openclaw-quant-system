import baostock as bs
import pandas as pd
import requests
from datetime import datetime, timedelta

CONFIG = {
    "max_positions": 3,
}

# ================= 股票池 =================
def load_pool():
    try:
        df = pd.read_csv("pool.csv")
        return df["code"].astype(str).tolist()
    except:
        print("❌ pool.csv错误")
        return []

# ================= 腾讯实时 =================
def get_realtime(codes):
    try:
        code_str = ",".join(["sh"+c if c.startswith("6") else "sz"+c for c in codes])
        url = f"http://qt.gtimg.cn/q={code_str}"

        res = requests.get(url, timeout=5)
        data = {}

        for line in res.text.split("\n"):
            if not line.strip():
                continue

            parts = line.split("~")
            if len(parts) < 35:
                continue

            code = parts[2]

            data[code] = {
                "name": parts[1],
                "price": float(parts[3]),
                "open": float(parts[5]),
                "preclose": float(parts[4]),
                "high": float(parts[33]),
                "low": float(parts[34])
            }

        return data

    except Exception as e:
        print("❌ 行情失败:", e)
        return {}

# ================= 指数情绪 =================
def get_index_sentiment():
    try:
        url = "http://qt.gtimg.cn/q=sh000905"  # 中证500
        res = requests.get(url, timeout=5).text

        parts = res.split("~")
        price = float(parts[3])
        pre = float(parts[4])

        pct = (price - pre) / pre * 100

        return pct
    except:
        return 0

# ================= K线 =================
def get_k(code):
    rs = bs.query_history_k_data_plus(
        code,
        "date,close,high,low,volume",
        start_date=(datetime.now()-timedelta(days=60)).strftime("%Y-%m-%d"),
        end_date=datetime.now().strftime("%Y-%m-%d"),
        frequency="d",
        adjustflag="3"
    )

    data = []
    while rs.next():
        data.append(rs.get_row_data())

    df = pd.DataFrame(data, columns=rs.fields)

    for col in ['close','high','low','volume']:
        df[col] = df[col].astype(float)

    return df

# ================= 龙头 =================
def is_leader(df):
    pct = df['close'].pct_change()*100
    return sum(1 for x in pct.tail(5) if x > 5) >= 2

# ================= 尾盘 =================
def tail_score(df, rt):
    prev = df.iloc[-2]

    close = rt["price"]
    high = rt["high"]
    low = rt["low"]

    s = 0

    if close > high * 0.96:
        s += 30

    if close > prev['close']:
        s += 20

    if (high - low) / low < 0.06:
        s += 20

    return s

# ================= 评分 =================
def calc_score(df, rt):
    s = 0
    tags = []

    vr = df['volume'].iloc[-1] / df['volume'].tail(5).mean()
    if vr > 1.3:
        s += 25
        tags.append("放量")

    if df['close'].iloc[-1] > df['close'].rolling(5).mean().iloc[-1]:
        s += 20
        tags.append("趋势")

    if is_leader(df):
        s += 25
        tags.append("强势")

    t = tail_score(df, rt)
    if t >= 30:
        s += 30
        tags.append("尾盘资金")

    return s, tags

# ================= 卖出策略 =================
def sell_strategy(rt):
    print("\n🚨 09:25卖出决策：")

    for code, r in rt.items():
        gap = (r["open"] - r["preclose"]) / r["preclose"] * 100

        if gap <= -3:
            action = "❌ 止损"
        elif gap >= 3:
            action = "🚀 止盈"
        else:
            action = "⏳ 持有观察"

        print(f"{code} {r['name']} 开盘:{gap:.2f}% 👉 {action}")

# ================= 主程序 =================
def run():
    now = datetime.now().strftime("%H:%M")
    print(f"🚀 V60 闭环系统启动 当前时间:{now}")

    codes = load_pool()
    if not codes:
        return

    rt_map = get_realtime(codes)
    if not rt_map:
        return

    # ===== 早盘卖出 =====
    if now < "09:30":
        sell_strategy(rt_map)
        return

    # ===== 下午买入 =====
    if now < "14:40":
        print("⏳ 未到买入时间")
        return

    # ===== 情绪判断 =====
    idx = get_index_sentiment()
    print(f"\n📊 中证500涨跌: {idx:.2f}%")

    if idx < -1:
        print("❌ 情绪差，今日空仓")
        return

    bs.login()

    results = []

    for code in codes:
        try:
            if code not in rt_map:
                continue

            bs_code = "sh."+code if code.startswith("6") else "sz."+code
            df = get_k(bs_code)

            s, tags = calc_score(df, rt_map[code])

            results.append({
                "code": code,
                "name": rt_map[code]["name"],
                "score": s,
                "reason": "|".join(tags),
                "price": rt_map[code]["price"]
            })

        except:
            continue

    bs.logout()

    df = pd.DataFrame(results).sort_values(by="score", ascending=False)
    picks = df.head(CONFIG["max_positions"])

    print("\n🔥 14:50买入建议：")

    weights = [0.5, 0.3, 0.2]

    for i, row in picks.iterrows():
        w = weights[i] if i < len(weights) else 0.1
        print(f"{row['code']} {row['name']} 分数:{row['score']} 仓位:{int(w*100)}% 理由:{row['reason']} 价格:{row['price']}")

if __name__ == "__main__":
    run()