import baostock as bs
import pandas as pd
import time
from datetime import datetime


def fetch_realtime_pool(stock_list):
    """
    批量获取当前交易日的实时行情
    """
    current_date = datetime.now().strftime('%Y-%m-%d')
    # 也可以用 baostock 动态获取最新交易日
    # current_date = bs.query_trade_dates(start_date=..., end_date=...).get_row_data()[0]

    results = []
    # 设定获取字段：开盘价(竞价)、最高、最低、当前价、成交量、换手率
    fields = "date,code,open,high,low,close,preclose,volume,turn,pctChg"

    for i, code in enumerate(stock_list):
        # 获取当天的日线数据
        # 在交易时间内，close 字段即为当前的最新价格
        rs = bs.query_history_k_data_plus(
            code, fields,
            start_date=current_date, end_date=current_date,
            frequency="d", adjustflag="3"
        )

        if rs.error_code == '0' and rs.next():
            row = rs.get_row_data()
            data = dict(zip(rs.fields, row))

            # 数据清洗：跳过停牌或未开盘的
            if not data['open'] or float(data['open']) == 0:
                continue

            # 计算核心指标
            open_p = float(data['open'])
            pre_close = float(data['preclose'])
            curr_p = float(data['close'])
            pct_chg = float(data['pctChg'])

            # 专家战法核心：计算高开幅度 (竞价强度)
            jump_pct = (open_p - pre_close) / pre_close * 100

            # 筛选逻辑：
            # 1. 竞价强势（高开 2%~5%）
            # 2. 实时依然强势（当前涨幅 > 5%）
            if jump_pct > 2.0 or pct_chg > 7.0:
                results.append({
                    '代码': data['code'],
                    '昨日收盘': pre_close,
                    '竞价开盘': open_p,
                    '竞价涨幅': f"{jump_pct:.2f}%",
                    '当前价格': curr_p,
                    '当前涨幅': f"{pct_chg:.2f}%",
                    '换手率': data['turn'],
                    '状态': "封板" if curr_p == float(data['high']) and pct_chg > 9.8 else "活跃"
                })

        if i % 50 == 0:
            print(f"扫描进度: {i}/{len(stock_list)}...")

    return pd.DataFrame(results)


# ====================== 执行 ======================
bs.login()

# 1. 获取选股池 (这里以沪深300+中证500为例)
all_codes = []
for func in [bs.query_hs300_stocks, bs.query_zz500_stocks]:
    rs = func()
    while rs.next():
        all_codes.append(rs.get_row_data()[1])
all_codes = list(set(all_codes))  # 去重

print(f"🚀 开始实时扫描 {len(all_codes)} 只标的...")
start_time = time.time()

df_result = fetch_realtime_pool(all_codes)

bs.logout()

# ====================== 输出 ======================
print("\n" + "!" * 30 + " 实时筛选结果 " + "!" * 30)
if not df_result.empty:
    # 按当前涨幅排序
    df_result['tmp_sort'] = df_result['当前涨幅'].str.replace('%', '').astype(float)
    df_result = df_result.sort_values('tmp_sort', ascending=False).drop('tmp_sort', axis=1)
    print(df_result.to_string(index=False))
else:
    print("当前暂无符合【强势竞价】或【长阳】逻辑的标的。")

print(f"\n耗时: {time.time() - start_time:.2f}秒")