import akshare as ak
import datetime

print("AKShare 测试...")
print(f"今日日期: {datetime.date.today()}")

# proxies = {'http': 'http://127.0.0.1:10809', 'https': 'http://127.0.0.1:10809'}



# 测试获取昨日涨停池
try:
    zt_df = ak.stock_zt_pool_em(date=(datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d"))
    print(f"昨日涨停家数: {len(zt_df) if not zt_df.empty else 0}")
    print(zt_df.head() if not zt_df.empty else "无数据")
except Exception as e:
    print(f"涨停池获取失败: {e}")