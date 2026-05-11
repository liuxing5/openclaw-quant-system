import os
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import warnings

# 屏蔽干扰警告
warnings.filterwarnings('ignore')

# ================= 1. 系统配置 =================
DB_CONFIG = {
    "host": os.getenv('POSTGRES_HOST', '49.233.189.132'),
    "port": os.getenv('POSTGRES_PORT', '5432'),
    "database": os.getenv('POSTGRES_DB', 'quant_system'),
    "user": os.getenv('POSTGRES_USER', 'quant'),
    "password": os.getenv('POSTGRES_PASSWORD', '')
}

STRATEGY_PARAMS = {
    "target_wealth": 1.30,
    "floor_wealth": 0.85,
    "gamma": 2.0,  # 已调低至2.0，增强稳健性
    "risk_free_rate": 0.02,
    "max_leverage": 0.75  # 增加硬性仓位上限，防止极端行情下的过载
}


# ================= 2. 数据引擎 =================
class DataEngine:
    def __init__(self):
        url = f"postgresql+psycopg2://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
        self.engine = create_engine(url)

    def get_market_data(self):
        # 获取最新交易日数据
        sql = """
            SELECT symbol, trade_date, close_price, high_price, low_price, pct_change, turnover_rate
            FROM daily_prices_cleaned 
            WHERE trade_date = (SELECT MAX(trade_date) FROM daily_prices_cleaned)
        """
        df = pd.read_sql(sql, self.engine)
        return df, df['trade_date'].iloc[0] if not df.empty else None


# ================= 3. 策略分析逻辑 =================
def run_v5_7_analysis():
    print("=" * 65)
    print("💎 Artur Sepp V5.7 稳健硬板版")
    print("=" * 65)

    engine = DataEngine()
    df, dt = engine.get_market_data()

    if df.empty:
        print("❌ 错误：无法获取数据。")
        return

    # A. 市场环境判定 (Regime Detection)
    # 统计封死涨停的数量
    limit_ups = df[(df['close_price'] == df['high_price']) & (df['pct_change'] > 9.5)]
    limit_up_count = len(limit_ups)
    avg_ret = df['pct_change'].mean()

    # 判定政权
    if limit_up_count >= 8:
        regime = {"name": "🔥 主升 (Growth)", "mu": 0.25, "sigma": 0.18}
    elif avg_ret < -1.0:
        regime = {"name": "❄️ 冰点 (Stress)", "mu": 0.02, "sigma": 0.25}
    else:
        regime = {"name": "🌪️ 震荡 (Neutral)", "mu": -0.05, "sigma": 0.20}

    # B. 仓位计算 (Artur Sepp 动态公式)
    current_w = 1.0
    funding_ratio = (current_w - STRATEGY_PARAMS["floor_wealth"]) / (
                STRATEGY_PARAMS["target_wealth"] - STRATEGY_PARAMS["floor_wealth"])

    # 使用调优后的 gamma = 2.0 计算 Merton 比例
    merton = (regime['mu'] - STRATEGY_PARAMS["risk_free_rate"]) / (STRATEGY_PARAMS["gamma"] * regime['sigma'] ** 2)
    suggested_pi = np.clip(merton * funding_ratio, 0, STRATEGY_PARAMS["max_leverage"])

    # C. 核心选股逻辑：只推荐“硬板”
    # 条件：1. 涨幅 > 5%  2. 收盘价 == 最高价 (封死涨停)
    candidates = df[(df['pct_change'] > 5.0) & (df['close_price'] == df['high_price'])].copy()

    # 评分模型：涨幅权重 80% + 换手率权重 20%
    candidates['score'] = candidates['pct_change'] * 0.8 + candidates['turnover_rate'].fillna(0) * 0.2
    top_picks = candidates.sort_values('score', ascending=False).head(3)

    # D. 输出报告
    print(f"[1] 交易日期: {dt}")
    print(f"[2] 市场环境: {regime['name']} (封板数: {limit_up_count})")
    print(f"[3] 安全系数: Funding Ratio = {funding_ratio:.4f}")
    print(f"[4] 理论仓位: {suggested_pi:.2%} (已应用 Gamma=2.0 稳健约束)")
    print("-" * 65)

    if suggested_pi > 0 and not top_picks.empty:
        print("🎯 核心推荐标的 (仅限封死最高价硬板):")
        for i, (_, row) in enumerate(top_picks.iterrows(), 1):
            is_20cm = " [20CM特高弹性]" if row['pct_change'] > 15 else ""
            print(
                f"  {i}. {row['symbol']}{is_20cm} | 现价: {row['close_price']:>7.2f} | 涨幅: {row['pct_change']:>6.2f}%")
    else:
        print("💤 操作建议：当前环境封板质量不佳或风险过高，建议观察。")
    print("=" * 65)


if __name__ == "__main__":
    run_v5_7_analysis()