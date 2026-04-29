import baostock as bs
import pandas as pd
import requests
import time
from datetime import datetime, timedelta

#实战操作建议：
#运行时间：明天下午 14:10 - 14:30 运行。
#买入动作：如果看到评分高的，且分时图正处于直线拉升或高位横盘，直接“现价+5分钱”下单抢筹。
#明日退出：高位法必看开盘。09:30 - 09:35 没能封板或没能维持在均线上方，一律出局，绝不幻想。
#其核心优势在于实时性，结合图片中石英股份 14:45 的走势，该脚本能帮你捕捉到正在主升浪中的股票。
# 但需注意：明日 09:35 必须清仓，因为高位法容错率极低。

# ====================== 1. 增强版实时行情获取 ======================
def get_realtime_quotes(stock_list):
    results = {}
    api_codes = [s.replace('.', '').lower() for s in stock_list]

    for i in range(0, len(api_codes), 50):
        chunk = api_codes[i:i + 50]
        url = f"http://qt.gtimg.cn/q={','.join(chunk)}"
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200: continue

            for line in resp.text.split(';'):
                if len(line) < 50: continue
                p = line.split('~')
                raw_code = p[0].split('=')[0][-8:]

                # 腾讯接口字段解析
                now_price = float(p[3])
                pct_chg = float(p[32])
                volume_hand = float(p[6]) * 100  # 换算为股
                turnover = float(p[38]) if p[38] else 0

                # 基础防御：过滤价格异常或今日未开盘的
                if now_price <= 0: continue

                results[raw_code] = {
                    'now': now_price,
                    'pct': pct_chg,
                    'vol': volume_hand,
                    'turn': turnover,
                    'high': float(p[33])
                }
            time.sleep(0.2)
        except Exception as e:
            print(f"⚠️ 实时数据抓取跳过一组: {e}")
            continue
    return results


# ====================== 2. 高位突破深度逻辑 ======================
def analyze_breakout_logic(hist_df, real, code):
    """
    针对高位博弈的逻辑修正
    """
    # 转换历史成交量为数值
    hist_vol = pd.to_numeric(hist_df['volume'], errors='coerce').fillna(0)
    prev_4d_avg_vol = hist_vol.tail(4).mean()

    curr_price = real['now']
    curr_pct = real['pct']
    curr_vol = real['vol']
    curr_turn = real['turn']

    # --- 第一层：硬性准入过滤 ---
    # 1. 涨幅区间：6.0% - 9.7% (避开已涨停或封板不牢的)
    if not (6.0 <= curr_pct <= 9.7):
        return None

    # 2. 动能校验：量比必须在 1.5 到 15 之间 (过滤掉数据异常的 200+ 量比)
    vol_ratio = curr_vol / prev_4d_avg_vol if prev_4d_avg_vol > 0 else 0
    if not (1.5 <= vol_ratio <= 15.0):
        return None

    # 3. 活跃度校验：换手率 3% - 20%
    if not (3.0 <= curr_turn <= 20.0):
        return None

    # --- 第二层：评分系统 ---
    score = 0
    tags = []

    # A. 趋势强度 (越接近涨停得分越高)
    if curr_pct >= 8.5:
        score += 40
        tags.append("临界爆发")
    else:
        score += 20
        tags.append("强势上攻")

    # B. 量能质量 (温和放量得高分，巨量次之)
    if 2.0 <= vol_ratio <= 5.0:
        score += 40
        tags.append("主力扫盘")
    elif vol_ratio > 5.0:
        score += 20
        tags.append("巨量博弈")

    # C. 价格位置 (收盘价即为最高价说明买盘极强)
    if curr_price >= real['high']:
        score += 20
        tags.append("光头大阳")

    return {
        'code': code,
        'price': curr_price,
        'pct': f"{curr_pct}%",
        'vol_ratio': round(vol_ratio, 2),
        'turn': f"{curr_turn}%",
        'score': score,
        'tags': " | ".join(tags)
    }


# ====================== 3. 主程序 ======================
def main():
    lg = bs.login()
    if lg.error_code != '0':
        print("Baostock登录失败")
        return

    print(f"📡 实时高位狙击版启动 | 时间: {datetime.now().strftime('%H:%M:%S')}")

    # 获取选股范围
    stock_pool = []
    rs = bs.query_zz500_stocks()
    while rs.next():
        stock_pool.append(rs.get_row_data()[1])

    # 1. 获取实时数据
    print(f"正在扫描实时行情 (样本: {len(stock_pool)})...")
    realtime_map = get_realtime_quotes(stock_pool)

    # 2. 深度逻辑筛选
    results = []
    start_d = (datetime.now() - timedelta(days=25)).strftime("%Y-%m-%d")
    end_d = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    for i, code in enumerate(stock_pool):
        key = code.replace('.', '').lower()
        if key not in realtime_map: continue

        # 预过滤，减少Baostock访问频率
        if not (6.0 <= realtime_map[key]['pct'] <= 9.7): continue

        k_rs = bs.query_history_k_data_plus(code, "date,code,close,volume",
                                            start_date=start_d, end_date=end_d,
                                            frequency="d", adjustflag="3")

        data_list = []
        while k_rs.next(): data_list.append(k_rs.get_row_data())

        if len(data_list) >= 10:
            df = pd.DataFrame(data_list, columns=k_rs.fields)
            res = analyze_breakout_logic(df, realtime_map[key], code)
            if res:
                results.append(res)
                print(f"✅ 命中信号: {code} | 评分: {res['score']} | 涨幅: {res['pct']}")

        if i % 20 == 0: time.sleep(0.1)

    bs.logout()

    # ====================== 4. 最终展示 ======================
    print("\n" + "🔥" * 5 + " 高位突破·隔夜优选清单 " + "🔥" * 5)
    if not results:
        print("当前市场环境下无符合高分条件的标的。")
    else:
        # 按得分降序，得分相同按量比降序
        df_res = pd.DataFrame(results).sort_values(by=['score', 'vol_ratio'], ascending=False)
        print(df_res.to_string(index=False))


if __name__ == "__main__":
    main()