# 七步漏斗选股策略 v1.0

## 架构总览

```
                    ┌──────────────────────────────┐
                    │    全市场股票池 (~5000只)       │
                    │    daily_quotes 日成交额>1亿    │
                    └──────────────┬───────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 0: 大盘风控（盘前）                                         │
│  ✅ 上涨≥2500家 + 全A指数>20EMA → 满仓                             │
│  ⚠️ 仅满足一项 → 仓位≤50%                                         │
│  ❌ 两项都不满足 → 当日不荐股                                       │
│  吸收: ③看大盘控仓位                                               │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 1: 硬性防雷                                                │
│  ✅ 剔除ST/*ST/退市股                                             │
│  ✅ 剔除上市<60天次新股                                            │
│  ✅ 流动比率>1.2  负债率<65%  营收同比≥-10%                        │
│  吸收: ①巴菲特准则/基本面                                          │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 2: 流动性筛选                                              │
│  ✅ 20日均成交额>1亿  流通市值>20亿  换手3~15%                      │
│  吸收: ③八步法                                                    │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 3: 趋势结构过滤                                            │
│  ✅ 周线CLOSE>20MA (100日均线)                                    │
│  ✅ EMA12>26>50 多头排列                                          │
│  ✅ 股价>EMA12（可容差）                                          │
│  ✅ 股价>年线(250MA) 加分                                         │
│  ✅ 上升平台/回踩支撑 加分                                         │
│  吸收: ②20周保命法/均线多头/年线定海神针/右侧交易                    │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 4: 动能与买入信号                                          │
│  ✅ 量比1.5~3  乖离率<6%                                          │
│  ✅ 需求吸收K线（EMA12附近锤子/刺透+放量）                         │
│  ✅ 强势接力（昨日首板，今日回踩VWAP翘头）                          │
│  ❌ 天量上轨禁止信号                                               │
│  吸收: ⑤价格行为/VWAP/一进二改良/布林反用                          │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 5: 人气精选                                                │
│  ✅ 综合评分≥80（基础50+涨幅+贴线+平稳+前层加分）                   │
│  ✅ 人气榜≤100 额外加分                                           │
│  吸收: ③隔夜八步法 + ⑥人气榜                                     │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Layer 6: 刚性风控                                                │
│  ✅ 买入时段≥14:30                                                │
│  ✅ 初始止损 = 入场价 - 1ATR                                      │
│  ✅ 移动止盈 = EMA12                                              │
│  ✅ 盈亏比 ≥ 2:1                                                  │
│  吸收: ⑦海龟ATR风控 + ③固定时段                                  │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
                    ┌──────────────────────────────┐
                    │     🎯 最终推荐 (Top 5)        │
                    │     含 ATR / 止损 / 目标价     │
                    └──────────────────────────────┘

           ┌──────────── 核心纪律④ ────────────┐
           │  每晚复盘 T+1 表现                  │
           │  任一步未满足 → 推倒重来            │
           │  连续3次止损失败 → 暂停交易一天      │
           └────────────────────────────────────┘
```

## 文件结构

```
strategies/funnel_strategy/
├── __init__.py                  # 包导出
├── funnel_config.py             # 全部参数配置 (FunnelConfig dataclass)
├── funnel_engine.py             # 主编排器 (FunnelEngine)
├── layer0_market_guard.py       # Layer 0: 大盘风控
├── layer1_fundamental_filter.py # Layer 1: 硬性防雷
├── layer2_liquidity_filter.py   # Layer 2: 流动性筛选
├── layer3_trend_filter.py       # Layer 3: 趋势结构过滤
├── layer4_momentum_filter.py    # Layer 4: 动能与买入信号
├── layer5_popularity_filter.py  # Layer 5: 人气精选
├── layer6_risk_control.py       # Layer 6: 刚性风控
├── daily_review.py              # 每日复盘模块
├── run_funnel.py                # 实盘运行入口
└── funnel_review_state.json     # 复盘状态持久化 (自动生成)
```

## 运行方式

### 基础用法

```bash
# 1. 完整七步漏斗（盘后运行）
python -m strategies.funnel_strategy.run_funnel

# 2. 指定日期
python -m strategies.funnel_strategy.run_funnel --date 2026-05-12

# 3. 指定输出目录
python -m strategies.funnel_strategy.run_funnel --output ./my_results
```

### 复盘闭环

```bash
# 4. 完整闭环：先复盘昨日T+1，再运行当日选股
python -m strategies.funnel_strategy.run_funnel --full-cycle

# 5. 仅复盘昨日推荐（不选股）
python -m strategies.funnel_strategy.run_funnel --review
```

### 调试模式

```bash
# 6. 跳过某几层（用层号 0-6）
python -m strategies.funnel_strategy.run_funnel --disable 0 4

# 7. 跳过 Layer 1 防雷（无财务数据时）
python -m strategies.funnel_strategy.run_funnel --disable 1
```

### 编程调用

```python
from strategies.funnel_strategy import run_funnel_strategy, FunnelConfig
from strategies.funnel_strategy.daily_review import DailyReviewer
from datetime import date

# 方式A: 一键运行
result = run_funnel_strategy(trade_date=date.today())

# 方式B: 自定义配置
cfg = FunnelConfig()
cfg.layer0_min_advancers = 2000    # 大盘偏弱时放宽
cfg.layer4_max_bias_pct = 8.0      # 放宽乖离
cfg.max_final_candidates = 3       # 只推荐3只

engine = FunnelEngine(cfg)
result = engine.run(trade_date=date.today())

# 方式C: 每日复盘
reviewer = DailyReviewer()
can_trade, reason = reviewer.is_trading_allowed()
report = reviewer.review_yesterday()
summary = reviewer.get_summary()
```

## 关键配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `layer0_min_advancers` | 2500 | 最小上涨家数 |
| `layer0_partial_cap` | 0.50 | 半仓比例 |
| `layer1_min_current_ratio` | 1.2 | 最小流动比率 |
| `layer1_max_debt_ratio` | 65.0 | 最大负债率% |
| `layer2_min_avg_amount_20d` | 1e8 (1亿) | 20日均成交额 |
| `layer2_min_circulating_mcap` | 2e9 (20亿) | 最小流通市值 |
| `layer3_ema_fast/mid/slow` | 12/26/50 | EMA参数 |
| `layer4_volume_ratio_min/max` | 1.5/3.0 | 量比范围 |
| `layer4_max_bias_pct` | 6.0 | 最大乖离率% |
| `layer5_min_composite_score` | 80 | 最低综合评分 |
| `layer6_atr_period` | 20 | ATR计算周期 |
| `layer6_initial_stop_atr` | 1.0 | 止损ATR倍数 |
| `layer6_min_profit_loss_ratio` | 2.0 | 最小盈亏比 |
| `max_final_candidates` | 5 | 最终最多推荐 |

## 输出格式

结果保存在 `./results/funnel_YYYYMMDD.csv`：

```
code, score, pct, entry_price, atr, stop_loss, target_price, profit_loss_ratio,
signal_type, tags, time_window_ok, max_position_pct
```

## 定时任务 (GitHub Actions)

```yaml
# 盘后 15:10 自动运行
# .github/workflows/funnel_strategy.yml
on:
  schedule:
    - cron: '10 7 * * 1-5'  # UTC 7:10 = 北京时间 15:10
```

## 依赖数据表

| 表名 | 用途 | 必需 |
|------|------|------|
| `daily_quotes` | 行情数据 (全流程) | ✅ |
| `stock_basic_info` | 股票基本信息 (ST/上市日) | ✅ |
| `stock_fundamentals` | 财务数据 (流动比率/负债率) | ⚠️ 缺失时放行 |
| `strong_stock_rank` | 人气排行 (Layer 5) | ⚠️ 缺失时无加分 |

## 注意事项

1. **盘后运行**：建议 15:10 后运行，此时行情数据已落库
2. **首次运行**：Layer 3 需要每只股票加载350日历史，全市场扫描可能耗时较长
3. **财务数据**：`stock_fundamentals` 表需通过 `llm_multisource` 采集管线填充
4. **复盘状态**：`funnel_review_state.json` 记录连续失败计数，不要手动删除
5. **连续失败暂停**：连续3次止损失败后自动暂停交易1天，手动清除状态可重置
