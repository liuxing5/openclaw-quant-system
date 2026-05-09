import baostock as bs
import pandas as pd
import requests
import time
from datetime import datetime, timedelta

#全场景兼容：一套代码支持 14:20 盘中狙击（实时模式）与 18:00 盘后复盘（准时模式）。
#逻辑双轨制：
#低位稳健（3%-5.5%）：追求蓄势起步，博弈首阳。
#高位爆发（6%-9.7%）：追求主力扫盘，博弈封板溢价。
#
#
#
#建议：明天 14:25 运行此脚本。如果看到得分超过 80 分 且带有 “黄金放量 | 贴线安全” 标签的票，那便是这四个版本合力筛选出的最强“种子选手”。
#
#


# ====================== 1. 策略配置中心 ======================
STRATEGY_CONFIG = {
    "pool_name": "中证500",
    "min_amount": 50000000,  # 成交额 > 5000万
    "vol_ratio_range": (1.5, 12.0),  # 合理量比区间
    "turnover_range": (3.5, 15.0),  # 黄金换手区间
    "max_pct": 9.7  # 封板保护：超过 9.7% 默认买不入，不推荐
}


# ====================== 2. 增强型实时行情采集 ======================
def get_realtime_snapshot(stock_list):
    results = {}
    api_codes = [s.replace('.', '').lower() for s in stock_list]
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    for i in range(0, len(api_codes), 50):
        chunk = api_codes[i:i + 50]
        url = f"http://qt.gtimg.cn/q={','.join(chunk)}"
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code != 200: continue

            for line in resp.text.split(';'):
                if len(line) < 50: continue
                p = line.split('~')
                raw_code = p[0].split('=')[0][-8:]
                results[raw_code] = {
                    'now': float(p[3]),
                    'pct': float(p[32]),
                    'vol': float(p[6]) * 100,
                    'turn': float(p[38]) if p[38] else 0,
                    'amount': float(p[37]) * 10000 if p[37] else 0,  # 转换为元
                    'high': float(p[33])
                }
            time.sleep(0.15)  # 优雅延时，保护IP
        except Exception:
            continue
    return results


# ====================== 3. 全能逻辑引擎 (双轨评分) ======================
def analyze_omni_logic(hist_df, real, code):
    # 数据准备
    hist_vol = pd.to_numeric(hist_df['volume'], errors='coerce').fillna(0)
    prev_4d_avg_vol = hist_vol.tail(4).mean()

    curr_price = real['now']
    curr_pct = real['pct']
    curr_vol = real['vol']
    curr_turn = real['turn']
    curr_amount = real['amount']

    # --- 第一层：硬性过滤器 ---
    if curr_pct < 3.0 or curr_pct > STRATEGY_CONFIG["max_pct"]: return None
    if curr_amount < STRATEGY_CONFIG["min_amount"]: return None

    vol_ratio = curr_vol / prev_4d_avg_vol if prev_4d_avg_vol > 0 else 0
    if not (STRATEGY_CONFIG["vol_ratio_range"][0] <= vol_ratio <= STRATEGY_CONFIG["vol_ratio_range"][1]):
        return None

    # --- 第二层：均线与趋势校验 ---
    df_c = hist_df.copy()
    df_c['close'] = pd.to_numeric(df_c['close'])
    # 计算MA5, MA10 (包含今日实时价格)
    close_list = df_c['close'].tolist() + [curr_price]
    ma5 = sum(close_list[-5:]) / 5
    ma10 = sum(close_list[-10:]) / 10

    if not (curr_price > ma5 > ma10): return None  # 必须是多头排列

    # --- 第三层：双轨动态评分 ---
    score = 0
    tags = []

    # 路径 A：稳健首阳 (3% - 5.5%)
    if 3.0 <= curr_pct <= 5.5:
        score += 30
        tags.append("稳健蓄势")
        if (curr_price - ma5) / ma5 < 0.02: score += 20; tags.append("贴线安全")

    # 路径 B：高位突破 (6% - 9.7%)
    elif 6.0 <= curr_pct <= 9.7:
        score += 40
        tags.append("高位博弈")
        if curr_price >= real['high']: score += 20; tags.append("光头大阳")

    # 通用加分项
    if 1.5 <= vol_ratio <= 4.0: score += 30; tags.append("黄金放量")
    if 5.0 <= curr_turn <= 12.0: score += 10; tags.append("换手活跃")

    if score >= 60:
        return {
            '代码': code, '现价': curr_price, '涨幅': f"{curr_pct}%",
            '量比': round(vol_ratio, 2), '换手': f"{curr_turn}%",
            '得分': score, '特征描述': " | ".join(tags)
        }
    return None


# ====================== 4. 自动化调度 ======================
def main():
    bs.login()
    print(f"💎 V44 Omni 终极版启动 | {datetime.now().strftime('%H:%M:%S')}")

    # 获取全量池
    rs = bs.query_zz500_stocks()
    stock_pool = []
    while rs.next(): stock_pool.append(rs.get_row_data()[1])

    # 1. 实时行情秒杀
    print(f"正在分析实时动态数据 (样本: {len(stock_pool)})...")
    real_map = get_realtime_snapshot(stock_pool)

    # 2. 深度历史验证
    results = []
    start_d = (datetime.now() - timedelta(days=25)).strftime("%Y-%m-%d")
    end_d = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    for i, code in enumerate(stock_pool):
        key = code.replace('.', '').lower()
        if key not in real_map: continue

        # 预过滤：如果不符合基础涨幅条件，直接跳过Baostock查询，保护频率
        if real_map[key]['pct'] < 3.0: continue

        k_rs = bs.query_history_k_data_plus(code, "date,close,volume", start_date=start_d, end_date=end_d,
                                            frequency="d", adjustflag="3")

        data = []
        while k_rs.next(): data.append(k_rs.get_row_data())

        if len(data) >= 10:
            df = pd.DataFrame(data, columns=k_rs.fields)
            res = analyze_omni_logic(df, real_map[key], code)
            if res:
                results.append(res)
                print(f"🎯 发现目标: {code} | 涨幅: {res['涨幅']} | 得分: {res['得分']}")

        if i % 30 == 0: time.sleep(0.05)

    bs.logout()

    # ====================== 5. 格式化输出 ======================
    print("\n" + "⭐" * 15 + " V44 Omni 隔夜精选池 " + "⭐" * 15)
    if not results:
        print("今日暂无符合终极逻辑的标的。")
    else:
        final_df = pd.DataFrame(results).sort_values(by='得分', ascending=False)
        print(final_df.to_string(index=False))
        print("\n💡 操作指引：")
        print("1. 路径A(稳健型): 寻找得分>70的贴线票，分批低吸。")
        print("2. 路径B(博弈型): 寻找带“主力扫盘”标签的票，次日09:35前决定去留。")


if __name__ == "__main__":
    main()