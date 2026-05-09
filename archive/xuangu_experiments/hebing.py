import baostock as bs
import pandas as pd
from datetime import datetime, timedelta
import time

# wenjian:只要涨 3% 且均线好就买	海选：只要是帅哥都行	容易选到“虚假繁荣”的弱势股
# gaoweitupoboyi:只买 7% 以上最猛的	追星：谁红买谁	经常满仓踏空（封板买不进）
# hebing:在 4% 附近寻找爆发前夜的种子	风险投资：找还没上市但最有潜力的公司	逻辑复杂，对数据完整性要求高
#

# ====================== 1. 配置与阈值 ======================
CONFIG = {
    "min_pct": 3.0,  # V44核心：最低涨幅
    "max_pct": 5.5,  # V44核心：最高涨幅（略放宽防止数据延迟）
    "turn_min": 3.0,  # 活跃换手底线
    "turn_max": 12.0,  # 换手过热线
    "vol_ratio_min": 1.2  # 量比底线（温和放量）
}

FIELDS = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"


# ====================== 2. 核心逻辑合并 ======================
def analyze_combined_logic(df, code):
    """
    合并版：V44的区间过滤 + 新版的量化评分
    """
    if df is None or len(df) < 10:
        return None

    # 数据清洗与防空处理
    df_clean = df.copy()
    for col in ['close', 'volume', 'pctChg', 'turn', 'amount', 'high']:
        df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce').fillna(0)

    last = df_clean.iloc[-1]
    prev_4d_avg_vol = df_clean.iloc[-5:-1]['volume'].mean()  # 过去4日均量

    curr_close = last['close']
    curr_pct = last['pctChg']
    curr_turn = last['turn']
    curr_vol = last['volume']
    curr_amount = last['amount']

    # --- 第一关：V44 硬性准入过滤 ---
    # 1. 涨幅必须在 3%-5.5% 之间（稳健区间）
    if not (CONFIG["min_pct"] <= curr_pct <= CONFIG["max_pct"]):
        return None

    # 2. 均线必须多头排列 (Close > MA5 > MA10)
    ma5 = df_clean['close'].rolling(5).mean().iloc[-1]
    ma10 = df_clean['close'].rolling(10).mean().iloc[-1]
    if not (curr_close > ma5 > ma10):
        return None

    # 3. 基础活跃度过滤
    if curr_turn < CONFIG["turn_min"] or curr_amount < 5e7:  # 成交额需 > 5000万
        return None

    # --- 第二关：Pro版量化评分系统 ---
    score = 0
    tags = []

    # A. 量比评分 (博弈动能)
    vol_ratio = curr_vol / prev_4d_avg_vol if prev_4d_avg_vol > 0 else 0
    if 1.5 <= vol_ratio <= 3.5:
        score += 40
        tags.append("温和放量")
    elif vol_ratio > 3.5:
        score += 20
        tags.append("巨量成交")
    elif vol_ratio >= 1.0:
        score += 10
        tags.append("量能达标")
    else:
        return None  # 量缩不买

    # B. 涨幅位置评分
    if curr_pct >= 4.5:
        score += 30
        tags.append("强势临界")
    else:
        score += 15
        tags.append("蓄势起步")

    # C. 换手率评分
    if 5.0 <= curr_turn <= 10.0:
        score += 30
        tags.append("黄金换手")

    # D. 均线乖离修正 (防止离5日线太远)
    if (curr_close - ma5) / ma5 < 0.03:
        score += 10
        tags.append("贴线运行(安全)")

    # --- 结果封装 ---
    if score >= 60:  # 综合得分门槛
        return {
            'code': code,
            'price': curr_close,
            'pct': curr_pct,
            'turn': curr_turn,
            'vol_ratio': round(vol_ratio, 2),
            'score': score,
            'tags': " | ".join(tags)
        }
    return None


# ====================== 3. 运行调度 ======================
def main():
    bs.login()

    print(f"🚀 V44 Pro 合并增强版启动 | 时间: {datetime.now().strftime('%H:%M:%S')}")

    # 获取池子 (中证500)
    stock_pool = []
    rs = bs.query_zz500_stocks()
    while rs.next():
        stock_pool.append(rs.get_row_data()[1])

    # 取最近15天数据确保均线准确
    today = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    results = []
    total = len(stock_pool)

    for i, code in enumerate(stock_pool):
        k_rs = bs.query_history_k_data_plus(code, FIELDS, start_date=start_date, end_date=today, frequency="d",
                                            adjustflag="3")

        data_list = []
        while k_rs.next():
            data_list.append(k_rs.get_row_data())

        if data_list:
            df = pd.DataFrame(data_list, columns=k_rs.fields)
            res = analyze_combined_logic(df, code)
            if res:
                results.append(res)

        if i % 50 == 0:
            print(f"进度: {i}/{total} | 已命中: {len(results)}")

    bs.logout()

    # ====================== 4. 最终展示 ======================
    print("\n" + "=" * 90)
    print(f"🔥 V44 Pro 最终隔夜精选清单 ({today}) 🔥")
    print("=" * 90)

    if not results:
        print("今日暂无符合条件的【稳健+高动能】标的。")
    else:
        # 按得分从高到低排序
        final_df = pd.DataFrame(results).sort_values('score', ascending=False)
        for _, row in final_df.iterrows():
            print(
                f"代码: {row['code']:<10} 价格: {row['price']:<8} 涨幅: {row['pct']:>5}%  量比: {row['vol_ratio']:>5}  得分: {row['score']:>3}")
            print(f"特征: {row['tags']}")
            print("-" * 90)


if __name__ == "__main__":
    main()