import baostock as bs
import pandas as pd
from datetime import datetime, timedelta
import time

#稳健筛选法:
#核心逻辑:  惯性溢价：利用缩量后的首阳，博弈次日惯性冲高。
#安全性:   高。回落空间有限，止损好设。
#爆发力	中。通常是稳步上涨，少有连板
#数据要求	对分时图的平稳度要求极高。
#适合环境	震荡市、慢牛行情。

# ====================== 基础配置 ======================
FIELDS = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"

CONFIG = {
    "min_pct": 3,
    "max_pct": 5,
    "turn_min": 5,
    "turn_max": 10,
    "stop_loss": -3,
    "take_profit": 6
}

# ====================== 交易日 ======================
def get_trade_date():
    today = datetime.now().date()
    for i in range(10):
        d = today - timedelta(days=i+1)
        if d.weekday() < 5:
            return d.strftime("%Y-%m-%d")
    return (today - timedelta(days=1)).strftime("%Y-%m-%d")

TRADE_DATE = get_trade_date()

print("="*100)
print("🚀 V44 隔夜推荐系统（最终版｜8步筛选）")
print("="*100)
print(f"📅 使用交易日: {TRADE_DATE}")

# ====================== 数据获取 ======================
def get_data(code, days=20):
    end_date = TRADE_DATE
    start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")

    rs = bs.query_history_k_data_plus(
        code,
        FIELDS,
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag="3"
    )

    data = []
    while (rs.error_code == '0') and rs.next():
        data.append(rs.get_row_data())

    if not data:
        return None

    df = pd.DataFrame(data, columns=rs.fields)
    df['pctChg'] = df['pctChg'].astype(float)
    df['turn'] = df['turn'].astype(float)
    df['volume'] = df['volume'].astype(float)
    df['close'] = df['close'].astype(float)
    df['preclose'] = df['preclose'].astype(float)

    # ===== 均线 =====
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()

    return df


# ====================== 个股分析（8步法） ======================
def analyze_stock(code):

    print("\n" + "-"*80)
    print(f"🔍 开始分析: {code}")

    df = get_data(code)
    if df is None or len(df) < 10:
        print("❌ 数据不足，淘汰")
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = last['close']
    pct = last['pctChg']
    turn = last['turn']

    # ====================== STEP 1：涨幅筛选 ======================
    print(f"STEP1 涨幅: {pct:.2f}%")

    if not (CONFIG["min_pct"] <= pct <= CONFIG["max_pct"]):
        print("❌ 不在3%-5%区间，淘汰")
        return None

    print("✅ 通过涨幅筛选")

    # ====================== STEP 2：量比（用成交量变化代替） ======================
    vol_ratio = last['volume'] / prev['volume'] if prev['volume'] > 0 else 0
    print(f"STEP2 量比(近似): {vol_ratio:.2f}")

    if vol_ratio < 1:
        print("❌ 量能不足，淘汰")
        return None

    print("✅ 通过量能筛选")

    # ====================== STEP 3：换手率 ======================
    print(f"STEP3 换手率: {turn:.2f}%")

    if not (CONFIG["turn_min"] <= turn <= CONFIG["turn_max"]):
        print("❌ 换手率不合格，淘汰")
        return None

    print("✅ 通过换手率")

    # ====================== STEP 4：流通市值（无API，近似过滤） ======================
    # 用成交额粗略替代
    amount = float(last['amount'])
    print(f"STEP4 成交额(替代市值): {amount:.2f}")

    if amount < 5e7 or amount > 5e9:
        print("❌ 资金规模不合适，淘汰")
        return None

    print("✅ 通过资金规模")

    # ====================== STEP 5：成交量趋势 ======================
    print("STEP5 成交量趋势判断")

    if last['volume'] < df['volume'].rolling(5).mean().iloc[-1]:
        print("❌ 量能不持续放大，淘汰")
        return None

    print("✅ 放量结构成立")

    # ====================== STEP 6：K线趋势 ======================
    print("STEP6 均线结构")

    if not (close > last['ma5'] and last['ma5'] > last['ma10']):
        print("❌ 均线不多头，淘汰")
        return None

    print("✅ 多头排列成立")

    # ====================== STEP 7：分时强势（用日线替代） ======================
    print("STEP7 强势确认")

    if pct < df['pctChg'].mean():
        print("❌ 不属于强势股，淘汰")
        return None

    print("✅ 强势成立")

    # ====================== STEP 8：尾盘入场点 ======================
    print("STEP8 入场信号")

    if pct < 4:
        print("⚠️ 非强尾盘突破，降低评级")

    print("✅ 符合隔夜条件")

    # ====================== 最终评分 ======================
    score = 0
    if pct >= 4: score += 3
    if turn > 7: score += 2
    if vol_ratio > 1.5: score += 2
    if close > last['ma5']: score += 2

    return {
        "code": code,
        "close": close,
        "pct": pct,
        "turn": turn,
        "score": score,
        "reason": "符合8步隔夜法条件"
    }


# ====================== 股票池 ======================
def get_stock_pool():
    print("\n📊 获取股票池...")

    stocks = set()

    rs = bs.query_zz500_stocks()
    while (rs.error_code == '0') and rs.next():
        stocks.add(rs.get_row_data()[1])

    print(f"股票池数量: {len(stocks)}")
    return list(stocks)


# ====================== 主程序 ======================
def main():

    lg = bs.login()
    if lg.error_code != '0':
        print("登录失败")
        return

    stock_list = get_stock_pool()

    results = []

    for i, code in enumerate(stock_list[:200]):  # 控制速度
        print(f"\n进度 {i+1}/200")

        try:
            res = analyze_stock(code)
            if res:
                print(f"🔥 推荐: {code} | 得分:{res['score']}")
                results.append(res)

        except Exception as e:
            print("异常:", e)

        time.sleep(0.1)

    bs.logout()

    # ====================== 输出 ======================
    print("\n" + "="*100)
    print("🔥 最终隔夜推荐（V44）")
    print("="*100)

    if not results:
        print("本次无符合条件股票")
        return

    results = sorted(results, key=lambda x: x["score"], reverse=True)

    for r in results:
        print(f"""
股票: {r['code']}
价格: {r['close']:.2f}
涨幅: {r['pct']:.2f}%
换手: {r['turn']:.2f}%
评分: {r['score']}

📌 推荐理由:
✔ 符合8步隔夜法
✔ 强势放量 + 多头趋势 + 资金活跃

🛑 止损: -3%
🎯 止盈: +6%
💰 仓位: 20%-40%（根据强度）
""")

    print("\n✅ V44运行完成")


if __name__ == "__main__":
    main()