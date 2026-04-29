import requests
import pandas as pd
from datetime import datetime

# =================== 你的持仓（每天修改这里） 第二天 9:25 运行脚本 ===================
positions = [
    # {"code": "601600", "cost": 12.66},
    {"code": "600151", "cost": 15.05}
]

# =================== 获取竞价数据 ===================
def get_open_price(codes):
    code_str = ",".join(["sh"+c if c.startswith("6") else "sz"+c for c in codes])
    url = f"http://qt.gtimg.cn/q={code_str}"

    res = requests.get(url)
    lines = res.text.split("\n")

    result = {}

    for line in lines:
        if not line.strip():
            continue

        data = line.split("~")
        if len(data) < 6:
            continue

        code = data[2]
        name = data[1]

        try:
            open_price = float(data[5])   # 今开
            pre_close = float(data[4])   # 昨收
        except:
            continue

        pct = (open_price - pre_close) / pre_close * 100

        result[code] = {
            "name": name,
            "open": open_price,
            "pct": pct
        }

    return result

# =================== 卖出策略 ===================
def decide_action(open_pct):
    if open_pct <= -2:
        return "❌ 竞价卖出", "低开风险"

    elif open_pct < 2:
        return "⚡ 冲高卖出", "无明显溢价"

    else:
        return "🚀 持有观察", "强势高开"

# =================== 主程序 ===================
def run():
    print("🚀 自动卖出系统启动（9:25版）")

    now = datetime.now()
    if not (915 <= now.hour * 100 + now.minute <= 930):
        print("⚠️ 当前非竞价时间（建议9:20~9:30运行）")

    codes = [p["code"] for p in positions]
    data = get_open_price(codes)

    print("\n📊 data：", data)

    results = []

    for p in positions:
        code = p["code"]
        cost = p["cost"]

        if code not in data:
            continue

        info = data[code]

        action, reason = decide_action(info["pct"])

        results.append({
            "代码": code,
            "名称": info["name"],
            "开盘价": round(info["open"], 2),
            "涨跌幅%": round(info["pct"], 2),
            "动作": action,
            "理由": reason
        })

    df = pd.DataFrame(results)

    print("\n📊 卖出建议：")
    print(df.to_string(index=False))


# =================== 执行 ===================
if __name__ == "__main__":
    run()