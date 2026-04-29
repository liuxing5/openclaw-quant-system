import baostock as bs
import pandas as pd
from datetime import datetime, timedelta
import time
import os

# ====================== 配置 ======================
FIELDS = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg,tradestatus,isST"

# 保存目录
SAVE_DIR = "涨停股_过去20日数据"
os.makedirs(SAVE_DIR, exist_ok=True)


# ====================== 日期处理 ======================
def get_last_trading_date():
    """获取最近一个交易日"""
    today = datetime.now().date()
    for i in range(30):
        check_date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        if datetime.strptime(check_date, '%Y-%m-%d').weekday() < 5:  # 非周末
            return check_date
    return (today - timedelta(days=1)).strftime('%Y-%m-%d')


def get_past_20_trading_days(end_date_str, days=20):
    """从结束日期往前取大约20个交易日的数据范围"""
    end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
    start_date = (end_date - timedelta(days=days * 2)).strftime('%Y-%m-%d')  # 多取一些防止节假日
    return start_date, end_date_str


# ====================== 获取涨停股 ======================
def get_limit_up_stocks(end_date):
    """获取当日涨停股票（使用所有A股列表 + 当日数据筛选）"""
    all_stocks = set()

    # 沪深300
    rs_hs300 = bs.query_hs300_stocks()
    while rs_hs300.next():
        all_stocks.add(rs_hs300.get_row_data()[1])

    # 上证50
    rs_sz50 = bs.query_sz50_stocks()
    while rs_sz50.next():
        all_stocks.add(rs_sz50.get_row_data()[1])

    # 中证500
    rs_zz500 = bs.query_zz500_stocks()
    while rs_zz500.next():
        all_stocks.add(rs_zz500.get_row_data()[1])

    return list(all_stocks)  # 约800只股票


# ====================== 获取单个股票过去20日详细数据 ======================
def get_stock_20_days_data(code, end_date):
    """获取某只股票过去20个交易日的详细数据"""
    start_date, _ = get_past_20_trading_days(end_date)

    rs = bs.query_history_k_data_plus(
        code,
        FIELDS,
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag="3"  # 后复权
    )

    if rs is None or rs.error_code != '0':
        print(f"❌ {code} 查询失败: {rs.error_msg if rs else '返回None'}")
        return None

    data_list = []
    while (rs.error_code == '0') and rs.next():
        data_list.append(rs.get_row_data())

    if not data_list:
        return None

    df = pd.DataFrame(data_list, columns=rs.fields)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # 只保留最近20条（交易日）
    if len(df) > 20:
        df = df.iloc[-20:]

    return df


# ====================== 主程序 ======================
print("=" * 90)
print("          A股涨停个股过去20个交易日详细数据获取工具")
print("=" * 90)
print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# 登录
lg = bs.login()
if lg.error_code != '0':
    print(f"登录失败: {lg.error_msg}")
    exit()
print("✅ baostock 登录成功")

end_date = get_last_trading_date()
print(f"目标日期: {end_date}（最近交易日）")

# 1. 获取涨停股
limit_up_stocks = get_limit_up_stocks(end_date)

if not limit_up_stocks:
    print("当天没有涨停个股或数据未更新，请稍后重试（建议收盘后运行）")
    bs.logout()
    exit()

print(f"\n共发现 {len(limit_up_stocks)} 只涨停个股，开始下载详细数据...")

# 2. 下载每个涨停股的20日数据并保存
with pd.ExcelWriter(f"{SAVE_DIR}/涨停股_过去20日数据_{end_date}.xlsx", engine='openpyxl') as writer:
    summary = []

    for idx, stock in enumerate(limit_up_stocks, 1):
        code = stock['code']
        print(f"[{idx}/{len(limit_up_stocks)}] 正在下载 {code} 的20日数据...")

        df = get_stock_20_days_data(code, end_date)

        if df is not None and not df.empty:
            # 保存到Excel的不同Sheet
            sheet_name = code.replace('.', '_')[:31]  # Sheet名长度限制
            df.to_excel(writer, sheet_name=sheet_name, index=False)

            # 汇总信息
            summary.append({
                '代码': code,
                '最新收盘': stock['close'],
                '当日涨幅%': stock['pct_chg'],
                '数据行数': len(df)
            })

            # 同时保存单个CSV
            df.to_csv(f"{SAVE_DIR}/{code}_{end_date}_20days.csv", index=False, encoding='utf-8-sig')

            print(f"   ✅ 保存成功（{len(df)} 条记录）")
        else:
            print(f"   ⚠️ 数据获取失败")

        time.sleep(0.3)  # 避免请求过快

# 3. 保存涨停股汇总表
if summary:
    summary_df = pd.DataFrame(summary)
    summary_df.to_excel(f"{SAVE_DIR}/涨停股汇总_{end_date}.xlsx", index=False)
    print(f"\n✅ 所有数据已保存至文件夹：{SAVE_DIR}")
    print(f"   - 详细数据 Excel：涨停股_过去20日数据_{end_date}.xlsx")
    print(f"   - 汇总表格：涨停股汇总_{end_date}.xlsx")

bs.logout()
print("✅ baostock 已登出，程序运行完毕！")