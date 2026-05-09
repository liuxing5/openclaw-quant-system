from Ashare import A
import pandas as pd
import datetime
import logging
import time
import random

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

print("\n" + "=" * 90)
print("          A股短线推荐 - Ashare 集成版（新浪/腾讯双源）")
print("=" * 90)
print(f"运行日期: {datetime.date.today()} | 数据源: Ashare\n")


# ====================== 获取股票列表（简化） ======================
def get_candidate_codes():
    """返回一些候选代码（实际使用时可手动输入或从其他方式获取全市场）"""
    # 这里先用常见活跃股 + 你可以扩展为从文件读取或手动输入
    base_codes = ["000001", "600519", "300750", "000858", "600036", "601318",
                  "002594", "000333", "601888", "600900"]
    # 如果你有昨日涨停列表，可以在这里手动添加代码
    print("提示：当前使用固定候选池，建议把昨日涨停股票代码添加到列表中。")
    return base_codes


# ====================== 数据获取与信号计算 ======================
def get_yesterday_data(code):
    """使用 Ashare 获取昨日日线数据"""
    try:
        # Ashare 返回 DataFrame，列名通常为 ['open','high','low','close','volume','amount' 等]
        df = A(code)  # 获取最新可用数据
        if df.empty:
            return None

        # 取最近一天（昨日或最新交易日）
        yesterday_row = df.iloc[-1] if len(df) > 0 else None
        if yesterday_row is None:
            return None

        return {
            "code": code,
            "pctChg": float(yesterday_row.get('close', 0)) / float(yesterday_row.get('pre_close',
                                                                                     yesterday_row.get('close',
                                                                                                       1))) - 1 if 'pre_close' in yesterday_row else 0,
            "turnover": float(yesterday_row.get('turnover', 0)) if 'turnover' in yesterday_row else 0,
            "volume": float(yesterday_row.get('volume', 0))
        }
    except Exception as e:
        logging.warning(f"Ashare 获取 {code} 数据失败: {e}")
        return None


def calc_signals(code):
    data = get_yesterday_data(code)
    if not data:
        return None

    pct = data["pctChg"] * 100  # 转百分比
    turnover = data["turnover"]

    sig = {}
    sig["昨日涨停"] = pct >= 9.7
    sig["昨日换手率"] = turnover
    sig["连板高度"] = 1 if pct >= 9.7 else 0  # 简化版，实际可多日计算
    sig["竞价强度模拟"] = 1.0 + (pct / 10)  # 简单模拟
    sig["炸板率低"] = (pct >= 9.5) and (5 < turnover < 18)
    sig["封单强度"] = 1 if turnover > 10 else 0

    return sig


def score_stock(sig):
    score = 0
    if sig.get("昨日涨停"): score += 4
    if sig.get("昨日换手率", 0) > 8: score += 3
    if sig.get("连板高度", 0) >= 2:
        score += 3
    elif sig.get("连板高度", 0) == 1:
        score += 1.5
    if sig.get("竞价强度模拟", 0) > 1.5: score += 2
    if sig.get("炸板率低"): score += 1.5
    if sig.get("封单强度", 0) > 0: score += 1
    return round(score, 1)


def reason_for_stock(sig, code):
    reasons = []
    if sig.get("昨日涨停"): reasons.append("昨日涨停")
    if sig.get("昨日换手率", 0) > 8: reasons.append(f"换手率 {sig['昨日换手率']:.1f}%")
    if sig.get("连板高度", 0) >= 1: reasons.append(f"连板 {sig['连板高度']}板")
    if sig.get("竞价强度模拟", 0) > 1.5: reasons.append("潜在竞价较强")
    if sig.get("炸板率低"): reasons.append("炸板率较低")
    reason_str = "；".join(reasons) if reasons else "信号一般"
    score = score_stock(sig)
    return f"【{code}】 分数: {score} | 理由: {reason_str}\n建议：结合东方财富查看今日竞价封单量，严格止损！"


# ====================== 主程序 ======================
if __name__ == "__main__":
    codes = get_candidate_codes()
    recs = []

    print(f"开始分析 {len(codes)} 只候选股票...\n")

    for i, code in enumerate(codes):
        print(f"处理 {code} ({i + 1}/{len(codes)})")
        sig = calc_signals(code)
        if sig and score_stock(sig) >= 7.0:  # 门槛可调整
            reason = reason_for_stock(sig, code)
            recs.append({"code": code, "score": score_stock(sig), "reason": reason})

        time.sleep(random.uniform(0.8, 1.5))  # 避免请求过快

    recs = sorted(recs, key=lambda x: x["score"], reverse=True)

    print("\n" + "=" * 80)
    print("【今日推荐结果】")
    if not recs:
        print("今日暂无达到分数门槛的股票（或数据获取不足）。")
        print("建议：手动把昨日涨停股票代码添加到 get_candidate_codes() 函数中。")
    else:
        for item in recs[:15]:
            print("\n" + item["reason"])

    print("\n" + "=" * 80)
    print("注意：当前使用 Ashare 数据源（新浪/腾讯）。")
    print("短线风险高，请结合实时盘口（封单量、炸板动态）决策，并严格止损。")
    print("本工具仅供学习参考，不构成投资建议。")