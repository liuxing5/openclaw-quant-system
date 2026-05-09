import baostock as bs
import pandas as pd
from datetime import datetime, timedelta
import time

#高位突破博弈法
#核心逻辑	强者恒强：利用大阳/涨停的势能，博弈次日高开。
#安全性	低。如果追高失败，次日容易遭遇大幅低开。
#爆发力	极强。选中的往往是当天的龙头或准龙头。
#数据要求	对封板强度和成交量比要求极高。
#适合环境	情绪高潮期、强势反弹行情。


# 14:10 左右运行：找出涨幅在 6% 左右、成交量比已经达标的票。
# 提前排队：在它还没涨停（比如 8%）的时候就买入，博弈它最后 20 分钟封板。
# 卖出要求更严：这种票明天开盘如果不强势，必须在 09:35 之前清仓，不能留恋。


# ====================== 配置 ======================
FIELDS = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"


def get_trading_days(days_count=5):
    """获取最近的交易日列表"""
    rs = bs.query_trade_dates(start_date=(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
                              end_date=datetime.now().strftime('%Y-%m-%d'))
    dates = []
    while (rs.error_code == '0') and rs.next():
        row = rs.get_row_data()
        if row[1] == '1':  # 是交易日
            dates.append(row[0])
    return dates[-days_count:]


def analyze_logic(df):
    """
    改进后的逻辑：增加数据合法性校验
    """
    # 1. 过滤掉成交量为空或为0的行（处理停牌情况）
    df = df[df['volume'].str.strip() != '']

    # 2. 检查有效数据是否足够计算均线
    if len(df) < 5:
        return None

    try:
        # 安全转换类型
        df['volume'] = df['volume'].astype(float)
        df['pctChg'] = df['pctChg'].astype(float)
        df['turn'] = pd.to_numeric(df['turn'], errors='coerce').fillna(0)
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)

        today = df.iloc[-1]
        # 计算过去4个有效交易日的平均成交量
        prev_4d_vol = df.iloc[-5:-1]['volume'].mean()

        curr_close = today['close']
        curr_high = today['high']
        curr_pct = today['pctChg']
        curr_turn = today['turn']
        curr_vol = today['volume']

        score = 0
        tags = []

        # --- 核心策略过滤 ---
        if curr_pct >= 9.8:
            score += 50
            tags.append("强势封板" if curr_close == curr_high else "曾触板")
        elif curr_pct >= 7.0:
            score += 30
            tags.append("长阳突破")
        else:
            return None

            # 量比分析
        vol_ratio = curr_vol / prev_4d_vol if prev_4d_vol > 0 else 0
        if 1.5 <= vol_ratio <= 4:
            score += 20
            tags.append("温和放量")
        elif vol_ratio > 4:
            score -= 10
            tags.append("巨量(警惕爆仓)")

        # 换手率分析
        if 3 <= curr_turn <= 12:
            score += 20
            tags.append("筹码高度活跃")
        elif curr_turn > 15:
            score -= 20
            tags.append("换手过热")

        if score >= 60:
            return {
                'code': today['code'],
                'price': curr_close,
                'pct': curr_pct,
                'turn': curr_turn,
                'vol_ratio': round(vol_ratio, 2),
                'tags': " | ".join(tags),
                'score': score
            }
    except Exception as e:
        # 捕获可能的计算异常，保证主程序不中断
        # print(f"计算出错: {e}")
        return None
    return None


# ====================== 主程序 ======================
lg = bs.login()

print("正在获取筛选池（中证500）...")
stock_pool = []
rs = bs.query_zz500_stocks()
while (rs.error_code == '0') and rs.next():
    stock_pool.append(rs.get_row_data()[1])

# 为了计算5日均量，多取一些日期以防中间有停牌
dates = get_trading_days(10)
start_d, end_d = dates[0], dates[-1]
print(f"分析周期: {start_d} 至 {end_d}")

results = []
for i, code in enumerate(stock_pool):
    if i % 50 == 0:
        print(f"进度: {i}/{len(stock_pool)}  已筛选: {len(results)}")

    # 增加重试机制或简单的错误处理
    k_rs = bs.query_history_k_data_plus(code, FIELDS, start_date=start_d, end_date=end_d, frequency="d", adjustflag="3")

    if k_rs.error_code != '0':
        continue

    data_list = []
    while k_rs.next():
        data_list.append(k_rs.get_row_data())

    if data_list:
        df_stock = pd.DataFrame(data_list, columns=k_rs.fields)
        res = analyze_logic(df_stock)
        if res:
            results.append(res)

    # 频率控制，防止被封
    # time.sleep(0.01)

bs.logout()

# ====================== 输出结果 ======================
print("\n" + "=" * 80)
print(f"🔥 隔夜战法精选结果 ({end_d}) 🔥")
if not results:
    print("今日无符合条件的股票。")
else:
    final_df = pd.DataFrame(results).sort_values('score', ascending=False)
    print(f"{'代码':<12} {'价格':<8} {'涨幅%':<8} {'量比':<8} {'特征描述'}")
    for _, row in final_df.iterrows():
        print(f"{row['code']:<12} {row['price']:<10.2f} {row['pct']:<10.2f} {row['vol_ratio']:<10.2f} {row['tags']}")

print("\n程序运行完毕！")