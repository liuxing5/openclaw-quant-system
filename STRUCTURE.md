# 项目结构

## 策略架构（四策略并行）

```
strategies/                           策略层（互相独立）
├── overnight_8step/                  ⭐ zuiyou1 隔夜8步法（生产核心）
│   ├── zuiyou1.py                    主引擎（v1.6.2, 双池策略, 量价规则引擎）
│   ├── sell_new.py                   自动卖出（v2.2, ATR止损+封板强度评估）
│   ├── notifyTelegram.py             Telegram推送
│   ├── position_manager.py           仓位管理
│   └── verify_prices.py              价格验证
├── llm_multisource/                  autorecommend 多源LLM策略
│   ├── collector.py                  5层信息采集（行情/研报/新闻/基本面/公告）
│   ├── aggregate.py                  每日候选聚合
│   ├── extractor.py                  推荐提取
│   └── pre_surge/                    recommend2 主升前夜（技术面短线）
├── resonance_filters/                ⭐ 5策略共振过滤
│   ├── technical_filters.py          20周线/均线多头/MACD/布林/年线
│   └── resonance_config.py          配置（含保守/标准/激进三档）
├── combine_strategies.py             三层编排（共振→LLM→八步法）
├── run_resonance_strategy.py         实盘入口（共振+LLM+八步法）
└── funnel_strategy/                  ⭐ 七步漏斗选股 v1.0（新框架）
    ├── funnel_config.py              FunnelConfig 全部参数
    ├── funnel_engine.py              主编排器 FunnelEngine
    ├── layer0_market_guard.py        Layer 0: 大盘风控
    ├── layer1_fundamental_filter.py  Layer 1: 硬性防雷
    ├── layer2_liquidity_filter.py    Layer 2: 流动性筛选
    ├── layer3_trend_filter.py        Layer 3: 趋势结构过滤
    ├── layer4_momentum_filter.py     Layer 4: 动能与买入信号
    ├── layer5_popularity_filter.py   Layer 5: 人气精选
    ├── layer6_risk_control.py        Layer 6: 刚性风控
    ├── daily_review.py               每日复盘模块（纪律④）
    └── run_funnel.py                 实盘运行入口

core/                                共享基础设施
├── db/
│   ├── connection.py                get_db() / db_configured()
│   ├── candidates.py                write_candidates() 统一 UPSERT 入口
│   ├── schema.sql                   主 schema（7个业务表）
│   ├── apply_schema.py              执行 schema.sql
│   └── cleanup_duplicates.py
├── notify/
│   └── pusher.py                    Telegram 推送
├── tracker/
│   └── performance.py               T+N 收益追踪
├── market_data/
│   └── quotes.py                    akshare 全市场行情采集
├── ui/
│   └── app.py                       Streamlit Web 看板
└── utils/
    ├── env.py                       load_project_env()
    ├── trading_calendar.py          交易日历
    └── ts_code.py                   代码格式转换

docs/strategy_notes/                 策略文档
├── FUNNEL_STRATEGY.md               七步漏斗运行文档
├── LLM_MULTISOURCE.MD               LLM多源策略
└── pre_surge/                       主升前夜策略

archive/                             历史代码（只读归档）
```

## 候选池统一表

`daily_candidates` 表加 `source` 字段，四种来源汇入同一张表：

| source | 来源策略 | 方法论 |
|---|---|---|
| `overnight_8step` | zuiyou1 | 量价规则引擎 |
| `llm_multisource` | autorecommend | LLM 多源分析 |
| `pre_surge` | recommend2 主升前夜 | 技术面短线 |
| `funnel_strategy` | 七步漏斗 v1.0 | 大盘→防雷→流动→趋势→动能→人气→风控 |

UNIQUE 约束：`(snapshot_date, ts_code, run_mode, source)` —— 同一日同一票不同策略各自一行。

## 七步漏斗策略 (funnel_strategy v1.0)

```
Layer 0: 大盘风控 → 上涨≥2500家 + 全A指数>20EMA → 否则仓位≤50%
Layer 1: 硬性防雷 → ST/次新/流动比率/负债率/营收
Layer 2: 流动性筛选 → 20日均额/市值/换手
Layer 3: 趋势结构 → 周线MA/EMA排列/年线/右侧
Layer 4: 动能信号 → 需求吸收K线/强势接力/量比/乖离
Layer 5: 人气精选 → 综合评分≥80 + 人气榜
Layer 6: 刚性风控 → ATR止损/盈亏比≥2:1/14:30入场

核心纪律④: 每日复盘T+1，连续3次止损失败暂停交易一天
```

## 调用约定

任何脚本要用 core 包，必须先 sys.path bootstrap：

```python
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db
```

## 已完成

- [x] Phase 1 — 目录骨架 + 归档死代码
- [x] Phase 2 — zuiyou1 生态迁入 strategies/overnight_8step/
- [x] Phase 3 — autorecommend 核心迁入 strategies/llm_multisource/
- [x] Phase 4 — core/ 共享层落地（db/notify/tracker/market_data/ui/utils）
- [x] Phase 5 — daily_candidates 加 source 字段，zuiyou1 + aggregate 双写共表
- [x] Phase 6 — recommend2 并入 strategies/llm_multisource/pre_surge/
- [x] Phase 7 — 5策略共振过滤模块 strategies/resonance_filters/
- [x] Phase 8 — 七步漏斗策略 v1.0 strategies/funnel_strategy/
