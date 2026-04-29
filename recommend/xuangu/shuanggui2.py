import baostock as bs
import pandas as pd
import requests
import time
from datetime import datetime, timedelta

# ====================== 1. 全局策略逻辑配置 ======================
CONFIG = {
    "MODE": "realtime",  # 盘中运行改 realtime，盘后复盘改 post
    "min_amount": 150000000,  # 提高门槛至 1.5 亿，过滤小盘庄股
    "max_vol_ratio": 10.0,  # 异常量比上限
}


# ====================== 2. 辅助工具：时间权重计算 ======================
def get_time_weight():
    """计算当前时间占全天交易时长的比例 (A股 240分钟)"""
    if CONFIG["MODE"] == "post": return 1.0

    now = datetime.now()
    h, m = now.hour, now.minute

    # 早盘 9:30 - 11:30
    if h == 9 and m >= 30:
        passed = m - 30
    elif h == 10:
        passed = 30 + m
    elif h == 11 and m <= 30:
        passed = 90 + m
    elif h == 11 and m > 30:
        passed = 120
    # 午盘 13:00 - 15:00
    elif h == 12:
        passed = 120
    elif h >= 13 and h < 15:
        passed = 120 + (h - 13) * 60 + m
    elif h >= 15:
        passed = 240
    else:
        passed = 1  # 防止除以0

    weight = passed / 240.0
    return max(0.05, min(1.0, weight))  # 限制区间


# ====================== 3. 核心数据引擎 ======================
def get_market_data(stock_list):
    """根据模式获取数据：盘中从腾讯拿，盘后从Baostock拿"""
    if CONFIG["MODE"] == "post":
        # 盘后模式：数据由 main 函数循环获取，此处返回空
        return {}

    # 实时模式：腾讯接口
    results = {}
    api_codes = [s.replace('.', '').lower() for s in stock_list]
    for i in range(0, len(api_codes), 50):
        chunk = api_codes[i:i + 50]
        url = f"http://qt.gtimg.cn/q={','.join(chunk)}"
        try:
            resp = requests.get(url, timeout=5)
            for line in resp.text.split(';'):
                if len(line) < 50: continue
                p = line.split('~')
                results[p[0].split('=')[0][-8:]] = {
                    'now': float(p[3]), 'pct': float(p[32]), 'vol': float(p[6]) * 100,
                    'turn': float(p[38]) if p[38] else 0, 'amount': float(p[37]) * 10000, 'high': float(p[33])
                }
            time.sleep(0.1)
        except:
            continue
    return results


# ====================== 4. 归一化评分与风险扣分逻辑 ======================
def analyze_logic_v2(hist_df, real_data, code):
    # 1. 基础数据
    hist_close = pd.to_numeric(hist_df['close'])
    hist_vol = pd.to_numeric(hist_df['volume'])
    prev_4d_avg_vol = hist_vol.tail(4).mean()

    # 2. 模式适配
    if CONFIG["MODE"] == "realtime":
        curr_price = real_data['now']
        curr_pct = real_data['pct']
        curr_vol = real_data['vol']
        curr_turn = real_data['turn']
        # --- 修正点：时间加权量比 ---
        weight = get_time_weight()
        est_full_vol = curr_vol / weight
        vol_ratio = est_full_vol / prev_4d_avg_vol if prev_4d_avg_vol > 0 else 0
    else:
        # 盘后直接取 K 线最后一行，不混用实时数据
        today = hist_df.iloc[-1]
        curr_price = float(today['close'])
        curr_pct = float(today['pctChg'])
        curr_vol = float(today['volume'])
        curr_turn = float(today['turn'])
        vol_ratio = curr_vol / prev_4d_avg_vol if prev_4d_avg_vol > 0 else 0

    # 3. 过滤器：硬性门槛
    if curr_pct < 3.0 or curr_pct > 9.8: return None
    if vol_ratio < 1.5 or vol_ratio > CONFIG["max_vol_ratio"]: return None

    # 4. 均线逻辑：采用昨收 MA 避免未来函数波动
    ma5_yest = hist_close.iloc[-6:-1].mean() if CONFIG["MODE"] == "realtime" else hist_close.iloc[-5:].mean()
    if curr_price < ma5_yest: return None

    # 5. 评分体系（加分 + 减分）
    score = 50  # 初始分
    tags = []

    # 加分项
    if 3.0 <= curr_pct <= 6.0: score += 10; tags.append("蓄势区")
    if 6.0 < curr_pct <= 9.5: score += 15; tags.append("爆发区")
    if 1.8 <= vol_ratio <= 4.0: score += 20; tags.append("量能健康")

    # --- 修正点：风险扣分机制 ---
    if curr_turn > 15.0: score -= 20; tags.append("换手过热扣分")
    if vol_ratio > 6.0: score -= 15; tags.append("放量过激扣分")
    # 乖离率过大扣分 (追高风险)
    bias = (curr_price - ma5_yest) / ma5_yest
    if bias > 0.08: score -= 20; tags.append("乖离过大扣分")

    if score >= 60:
        return {'代码': code, '现价': curr_price, '涨幅': curr_pct, '修正量比': round(vol_ratio, 2), '得分': score,
                '特征': "|".join(tags)}
    return None


# ====================== 5. 主执行程序 ======================
def main():
    bs.login()
    print(f"📡 V44 Omni Pro 启动 | 模式: {CONFIG['MODE']} | 时间权重: {get_time_weight():.2f}")

    rs = bs.query_zz500_stocks()
    pool = []
    while rs.next(): pool.append(rs.get_row_data()[1])

    real_map = get_market_data(pool) if CONFIG["MODE"] == "realtime" else {}
    results = []

    for i, code in enumerate(pool):
        # 灵活获取 K 线
        end_date = datetime.now().strftime("%Y-%m-%d")
        k_rs = bs.query_history_k_data_plus(code, "date,close,volume,pctChg,turn",
                                            start_date=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
                                            end_date=end_date, frequency="d", adjustflag="3")

        data = []
        while k_rs.next(): data.append(k_rs.get_row_data())
        if len(data) < 10: continue

        df = pd.DataFrame(data, columns=k_rs.fields)

        # 核心逻辑调用
        res = analyze_logic_v2(df, real_map.get(code.replace('.', '').lower()), code)
        if res: results.append(res)

        if i % 50 == 0: print(f"进度: {i}/{len(pool)}")

    bs.logout()

    if results:
        final_df = pd.DataFrame(results).sort_values(by='得分', ascending=False)
        print("\n" + "⭐" * 30 + "\n", final_df.to_string(index=False))
    else:
        print("\n当前条件下无符合标的。")


if __name__ == "__main__":
    main()