import requests

# 14:30 实时全市场个股数据（最新价、涨跌、成交量）
def get_realtime_all():
    url = "https://hq.sinajs.cn/rn=1/l=sh_a,sz_a"
    headers = {"Referer": "https://finance.sina.com/"}
    res = requests.get(url, headers=headers, timeout=3)
    lines = res.text.split("\n")
    stock_list = []
    for line in lines:
        if "=" not in line: continue
        code = line.split("=\"")[0][-6:]
        data = line.split(",")
        if len(data) < 7: continue
        name = data[0].split('"')[-1]
        price = data[3]
        change = data[4]
        stock_list.append({"code": code, "name": name, "price": price})
    return stock_list

# 使用
realtime = get_realtime_all()
for stock in realtime[:10]:
    print(f"【实时】{stock['code']} {stock['name']} 价格: {stock['price']}")