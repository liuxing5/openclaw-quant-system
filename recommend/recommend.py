import pandas as pd
import numpy as np
import psycopg2
import math
import warnings

warnings.filterwarnings('ignore')

# ================= 1. 终极配置 (生产级风控) =================
DB_CONFIG = {
    "host": "49.233.189.132", "port": "5432",
    "database": "quant_system", "user": "quant", "password": "d1cf4fce072f6fc6aeb79dae"
}

INIT_CASH = 100000.0
MAX_POSITIONS = 5  # 回归 5 只，平衡波动
STOP_LOSS_PCT = -7.0  # 单股止损
GLOBAL_STOP_LOSS = -8.5  # 账户净值保护：从最高点回撤 8.5% 触发全系统强制空仓 10 天
FEE_RATIO = 0.0012
SLIPPAGE = 0.0004


# ================= 2. 数据引擎：引入 ATR 波动率 =================
def fetch_final_data():
    print("💎 V46 终极版启动：正在构建多维特征矩阵...")
    conn = psycopg2.connect(**DB_CONFIG)
    query = """
        WITH csi300 AS (
            SELECT symbol FROM daily_prices 
            WHERE trade_date = '2025-12-31' 
            ORDER BY (turnover_rate * volume) DESC LIMIT 300
        )
        SELECT symbol, trade_date, open_price as open, close_price as close, 
               high_price as high, low_price as low, turnover_rate as turn, volume as vol
        FROM daily_prices WHERE symbol IN (SELECT symbol FROM csi300) ORDER BY symbol, trade_date ASC
    """
    df = pd.read_sql(query, conn)
    conn.close()

    df['dt'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
    groups = df.groupby('symbol')

    # 核心指标：MA20, RS_20, 以及用于风险平价的 ATR
    df['ma20'] = groups['close'].transform(lambda x: x.rolling(20).mean())
    df['tr'] = np.maximum(df['high'] - df['low'],
                          np.maximum(abs(df['high'] - df['close'].shift(1)),
                                     abs(df['low'] - df['close'].shift(1))))
    df['atr'] = groups['tr'].transform(lambda x: x.rolling(20).mean())
    df['rs_20'] = groups['close'].transform(lambda x: x.pct_change(20))
    df['pre_close'] = groups['close'].shift(1)

    date_map = {d: group.set_index('symbol').to_dict('index') for d, group in df.groupby('dt')}
    return df, date_map


# ================= 3. 策略引擎：净值熔断 + 风险平价 =================
def run_final_singularity(backtest_days=250):
    df_all, date_map = fetch_final_data()
    all_dates = sorted(list(date_map.keys()))
    test_dates = all_dates[-backtest_days:]

    cash, positions, equity_curve, trade_log = INIT_CASH, [], [], []
    peak_equity = INIT_CASH
    cooldown_until = ""  # 全局熔断冷却期

    for i in range(len(test_dates) - 1):
        t_day, t_next = test_dates[i], test_dates[i + 1]
        day_snap = date_map.get(t_day, {})
        next_snap = date_map.get(t_next, {})

        # 计算当前总资产
        curr_mv = sum(
            [p['shares'] * day_snap.get(p['symbol'], {'close': p['entry_price']})['close'] for p in positions])
        total_equity = cash + curr_mv
        peak_equity = max(peak_equity, total_equity)
        equity_curve.append({'total': total_equity})

        # 1. 全局账户熔断检查 (这是防止爆仓的最后一道墙)
        drawdown = (total_equity - peak_equity) / peak_equity * 100
        if drawdown < GLOBAL_STOP_LOSS:
            if positions:  # 触发熔断：全部清仓
                for pos in positions:
                    if pos['symbol'] in next_snap:
                        sell_p = next_snap[pos['symbol']]['open'] * (1 - SLIPPAGE)
                        cash += pos['shares'] * sell_p * (1 - FEE_RATIO)
                positions = []
                cooldown_until = all_dates[min(i + 15, len(all_dates) - 1)]  # 强制休息 15 个交易日
                print(f"🚨 {t_day} 触发全局净值熔断！强制空仓至 {cooldown_until}")

        if t_day < cooldown_until: continue  # 冷却期内不操作

        # 2. 卖出逻辑
        still_holding = []
        for pos in positions:
            if pos['symbol'] in day_snap:
                r = day_snap[pos['symbol']]
                pnl = (r['close'] - pos['entry_price']) / pos['entry_price'] * 100

                # 退出逻辑：跌破 MA20 或 触发单股止损
                if pnl <= STOP_LOSS_PCT or r['close'] < r['ma20']:
                    if pos['symbol'] in next_snap:
                        sell_p = next_snap[pos['symbol']]['open'] * (1 - SLIPPAGE)
                        cash += pos['shares'] * sell_p * (1 - FEE_RATIO)
                        continue
            still_holding.append(pos)
        positions = still_holding

        # 3. 买入逻辑：只选 RS 最强的 5 只，且通过 ATR 调整仓位
        needed = MAX_POSITIONS - len(positions)
        if needed > 0 and day_snap:
            # 市场广度过滤
            ups = [s for s, r in day_snap.items() if r['close'] > r.get('pre_close', 0)]
            if (len(ups) / len(day_snap)) > 0.4:
                candidates = []
                for sym, r in day_snap.items():
                    if r['close'] > r['ma20'] and r['turn'] > 0.8:
                        if not pd.isna(r['rs_20']) and r['atr'] > 0:
                            candidates.append({'sym': sym, 'rs': r['rs_20'], 'atr': r['atr']})

                targets = sorted(candidates, key=lambda x: x['rs'], reverse=True)[:needed]
                for t in targets:
                    if t['sym'] in next_snap:
                        buy_p = next_snap[t['sym']]['open'] * (1 + SLIPPAGE)
                        # 风险平价：波动大的少买，波动小的多买
                        risk_unit = total_equity * 0.01 / (t['atr'] * 2)  # 单笔风险控制在总资金的 1%
                        slot_cash = min(risk_unit * buy_p, total_equity / MAX_POSITIONS)

                        if cash >= slot_cash:
                            shs = math.floor(slot_cash / (buy_p * (1 + FEE_RATIO)) / 100) * 100
                            if shs >= 100:
                                cash -= shs * buy_p * (1 + FEE_RATIO)
                                positions.append({'symbol': t['sym'], 'shares': shs, 'entry_price': buy_p})

        if i % 20 == 0:
            print(
                f"📅 {t_day} | 净值: {total_equity:,.2f} | 回撤: {drawdown:.2f}% | 状态: {'休眠' if t_day < cooldown_until else '运行'}")

    df_res = pd.DataFrame(equity_curve)
    ret = (df_res['total'].iloc[-1] / INIT_CASH - 1) * 100
    mdd = ((df_res['total'] - df_res['total'].cummax()) / df_res['total'].cummax()).min() * 100
    print(f"\n💎 V46 终极版结果: 累计收益 {ret:.2f}% | 最大回撤 {mdd:.2f}% | 运行状态：完成")


if __name__ == "__main__":
    run_final_singularity(250)