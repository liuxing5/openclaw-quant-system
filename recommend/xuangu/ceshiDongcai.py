import requests
import time

def test_zt_pool():
    print("🔍 测试涨停池接口（最终修复版）...")

    url = "http://push2ex.eastmoney.com/getTopicZTPool"

    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": "0",
        "pagesize": "50"
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "http://quote.eastmoney.com/",
    }

    try:
        res = requests.get(url, params=params, headers=headers, timeout=5)

        # ✅ 关键1：先看原始文本（防止JSON失败）
        if not res.text:
            print("❌ 返回为空")
            return

        # ✅ 关键2：尝试解析JSON
        try:
            data = res.json()
        except Exception:
            print("❌ 返回不是JSON（被拦截）")
            print(res.text[:200])
            return

        # ✅ 关键3：严谨判断
        if not data or "data" not in data:
            print("❌ data字段不存在")
            print(data)
            return

        if data["data"] is None:
            print("❌ data为None（常见：非交易时间）")
            return

        if "pool" not in data["data"]:
            print("❌ 没有pool字段")
            print(data["data"])
            return

        pool = data["data"]["pool"]

        if not pool:
            print("⚠️ pool为空（今天可能没涨停 or 接口异常）")
            return

        print(f"\n✅ 成功！涨停数量: {len(pool)}")

        for stock in pool[:5]:
            print(f"{stock.get('c')} {stock.get('n')} 炸板:{stock.get('f26')}")

    except Exception as e:
        print("❌ 请求失败:", e)


if __name__ == "__main__":
    test_zt_pool()