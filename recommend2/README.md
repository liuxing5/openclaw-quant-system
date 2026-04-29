# OpenClaw 主升前夜策略 (Pre-Main-Uptrend Strategy)

对原始"四特征"主观总结的量化重构,集成进 openclaw-quant-system。

## 设计要点

- **数据源**: baostock(单一数据源,Tushare/akshare 都已替换)
- **MACD 参数**: 8/17/9 (项目短线标准)
- **风控**: 2% 单笔风险 / 30% 现金储备 / T+1 / 单日 5% 熔断 / 总回撤 15% 熔断
- **复权**: 后复权(避免除权污染均线和突破判断)
- **PIT**: 严格 point-in-time,所有信号在 T 日收盘后产生,T+1 开盘成交

## 数据源说明 (baostock 局限)

baostock 是免费、稳定、PIT 友好的数据源,但有几个能力空缺需要明确:

| 数据 | baostock 状态 | 本策略处理 |
|---|---|---|
| 日 K 线(OHLCV+复权) | ✓ `query_history_k_data_plus` | L1-L7, L9 全部基于此 |
| 大盘指数 | ✓ 同上 | L11 大盘环境检查 |
| 全市场股票列表 | ✓ `query_all_stock` | scan 命令的 universe |
| 个股基本信息 | ✓ `query_stock_basic` | 上市日期等 |
| **主力资金流(L8)** | ✗ **无接口** | **默认跳过该层(allow_l8_missing=True)** |
| **龙虎榜(L8.5)** | ✗ **无接口** | **默认跳过该层(allow_lhb_missing=True)** |
| **流通市值** | ✗ **无直接字段** | scan 不在 universe 阶段过滤,依赖 L10 间接处理 |

因此实际可计分的层是 **10 层**(L1-L7, L9-L11),触发阈值默认 8/10。
若以后接入资金流和龙虎榜数据(比如自己跑爬虫存到本地数据库),
只需在 `data_loader.py` 替换 `get_money_flow` 和 `get_lhb_institution_flow` 实现即可,
筛选器逻辑无需改动。

## 12 层筛选器(baostock 模式下 L8/L8.5 默认跳过)

| 层 | 名称 | 核心阈值 | baostock 状态 |
|---|---|---|---|
| L1 | 底部定义 | 距 250 日高点回撤 ≥25% 且距 60 日低点 ≤20%(可调,熊市末期 0.35,牛市初期 0.20) | 启用 |
| L2 | 低位涨停痕迹 | 60 日内涨停且涨停时距低点 ≤15% | 启用 |
| L3 | 有效跳空缺口 | 缺口 ≥1.5% + 量比 ≥1.5 + 5 日不回补 | 启用 |
| L4 | 连阳质量 | ≥4 连阳且不破 5MA | 启用 |
| L5 | 突破日倍量 | 当日量 ≥ 20MA 量 ×2.0 | 启用 |
| L6 | 量能持续 | 近 3 日 ≥2 日量 ≥ 20MA 量 ×1.5 | 启用 |
| L7 | MACD 共振 | 金叉或 DIF 上穿零轴 | 启用 |
| L8 | 主力资金流 | 5 日净流入 > 0 且当日净流入 > 0 | **跳过(无数据)** |
| L8.5 | 龙虎榜机构席位 | 近 30 日机构净买 > 0 | **跳过(无数据)** |
| L9 | 剔除高位 | 收盘高于 60MA ≤25% | 启用 |
| L10 | 风控过滤 | 非 ST,上市 ≥250 日 | 启用 |
| L11 | 大盘环境 | 沪深 300 在 20MA 之上或 20MA 上行 | 启用 |

实际计分层 10 个,通过 ≥8 层视为触发(`ScreenerConfig.min_layers_to_trigger=8`)。

## 一字板剔除

回测引擎在 `try_open` 之前调用 `_can_buy_next_open`:

| 次日开盘形态 | 行为 |
|---|---|
| 涨幅 ≥9.5% **且** 当日振幅 <1% | 跳过(认定为一字涨停板,买不到) |
| 涨幅 ≥9.5% **但** 当日有振幅 | 允许买入(开板可成交) |
| 跌幅 ≥9.5% | 跳过(跌停一字板) |
| 其他 | 正常买入 |

可通过 `BacktestConfig.skip_one_word_limit=False` 关闭(用于对照测试)。

## 6 维退出器

| 编号 | 类型 | 触发 |
|---|---|---|
| E1 | 硬止损 | 浮亏 ≥8%(对应 2% 账户风险 + 25% 单仓) |
| E2 | 移动止盈 | 浮盈 ≥8% 后从最高点回撤 ≥5% |
| E3 | 时间止损 | 持仓 ≥15 个交易日 |
| E4 | MACD 死叉 | DIF 下穿 DEA |
| E5 | 破位放量 | 跌破 8MA 且量比 ≥1.2 |
| E6 | 高量阴线 | 量比 ≥3 且收阴 |

## 文件结构

```
openclaw_overnight/
├── config.py           # 全部配置(策略/风控/回测)
├── data_loader.py      # AKShare 封装 + 缓存 + 重试
├── indicators.py       # 指标库(MACD/缺口/连阳/量比)
├── screener.py         # 11 层筛选器
├── exitor.py           # 6 维退出器 + Position
├── portfolio.py        # 风控 + 组合管理
├── backtester.py       # Walk-Forward 回测引擎
├── main.py             # CLI 入口
├── test_smoke.py       # 冒烟测试(无网)
├── data/               # AKShare 缓存
├── results/            # 回测/扫描输出
└── logs/               # 日志
```

## 快速开始

```bash
# 1. 装依赖
pip install baostock pandas numpy pyarrow

# 2. 跑冒烟测试(无网,验证逻辑)
python main.py test

# 3. 扫描样本(快速验证 baostock 通路)
python main.py scan --sample

# 4. 全市场扫描(慢,需 5000+ 只股票)
python main.py scan

# 5. 样本回测
python main.py backtest --sample --start 2024-06-01 --end 2024-12-31

# 6. 全市场回测(限制前 200 只)
python main.py backtest --start 2024-01-01 --end 2024-12-31 --universe-limit 200
```

注意:
- baostock 服务器在国内,海外服务器/受限网络环境可能连不上
- 第一次跑会从 baostock 下载数据,后续走本地 parquet 缓存(默认 6 小时 TTL)
- baostock 单次返回数据量有限制,代码已通过缓存和 retry 应对

## 集成进 openclaw-quant-system

1. 拷贝目录到主仓 `strategies/pre_main_uptrend/`
2. 在主调度器(AGENTS.md 的 cron)注册:
   - 每周一 09:35 跑 `scan`,把触发列表推到小Q QQ
   - 每月最后一个周五跑 walk-forward 滚动验证
3. 实盘前先用 `paper_trading` 模式在富途 NiuNiu 跑一个月

## 已知边界

- 资金流接口偶尔失败,设计上允许 L8 缺失(可在 `ScreenerConfig.allow_l8_missing=False` 收紧)
- 龙虎榜近 30 日未上榜视为中性,不淘汰(可在 `ScreenerConfig.lhb_required=True` 收紧,只买入榜票)
- 涨停判定按收盘涨幅,会漏掉炸板涨停日
- 一字板剔除已实现:涨停一字板(开盘+9.5%且振幅<1%)和跌停一字板都会跳过买入
- 龙虎榜接口 `ak.stock_lhb_detail_em` 偶有列名变动,代码做了兼容性 rename,但仍可能需要维护
