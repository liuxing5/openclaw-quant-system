import datetime

print("\n" + "="*80)
print("          A股短线推荐 - 手动输入版（数据接口不可用时使用）")
print("="*80)
print(f"今日日期: {datetime.date.today()}")
print("由于 baostock 和 AKShare 当前均无法获取数据，请按以下步骤操作：\n")

print("步骤：")
print("1. 打开东方财富或同花顺")
print("2. 查看【昨日涨停板】列表")
print("3. 把你感兴趣的股票代码复制到下方（一行一个）")
print("4. 输入完毕后按两次回车结束\n")

### quant_user_pass

stocks = []
while True:
    code = input("请输入股票代码 (如 000001)，输入空行结束: ").strip()
    if not code:
        break
    if len(code) == 6 and code.isdigit():
        stocks.append(code)
    else:
        print("请输入6位数字股票代码！")

if not stocks:
    print("未输入任何股票，程序结束。")
else:
    print("\n" + "="*60)
    print("分析结果（简单版）")
    print("="*60)
    for code in stocks:
        print(f"\n【{code}】")
        print("建议：打开该股票K线图，查看昨日是否涨停、换手率、是否连板")
        print("短线关注点：竞价是否高开、封单量大小、所属板块是否强势")
        print("操作建议：严格止损（-5%或破昨日低点），控制仓位")