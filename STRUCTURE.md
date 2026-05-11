# 项目结构

## 三层架构

```
strategies/                  策略层（互相独立）
├── overnight_8step/         ⭐ zuiyou1 隔夜8步法（生产核心）
└── llm_multisource/         autorecommend 多源LLM策略
    └── pre_surge/           recommend2 主升前夜（技术面短线）

core/                        共享基础设施（两边复用，不用 platform/ 因与 stdlib 同名）
├── db/
│   ├── connection.py        get_db() / db_configured()
│   ├── candidates.py        write_candidates() 统一 UPSERT 入口
│   ├── schema.sql           主 schema（含 daily_candidates.source 列）
│   └── apply_schema.py      执行 schema.sql（一次性发送，psycopg2 自动切分）
├── notify/
│   └── pusher.py            Telegram 推送（每日候选池 + 异动告警）
├── tracker/
│   └── performance.py       T+N 收益追踪
├── market_data/
│   └── quotes.py            akshare 全市场行情采集（兜底 baostock/yfinance）
├── ui/
│   └── app.py               Streamlit Web 看板
└── utils/
    ├── env.py               load_project_env() 自动向上查找 .env
    └── ts_code.py           baostock_to_standard / standard_to_baostock

frameworks/quant_system/     量化框架库（保留，未来再用）

archive/                     历史代码（只读归档）
├── recommend_legacy/        recommend/ 顶层旧脚本
├── xuangu_experiments/      xuangu/ 下实验性脚本
└── broken_scripts/          API 不兼容已坏的脚本

tools/oneoff/                一次性维护脚本（修历史数据、清理重复等，不进 cron）
```

## 候选池统一表（合并核心）

`daily_candidates` 表加 `source` 字段，三种来源汇入同一张表：

| source | 来源策略 | 方法论 |
|---|---|---|
| `overnight_8step` | zuiyou1 | 量价规则引擎 |
| `llm_multisource` | autorecommend collector + extractor | LLM 多源分析 |
| `pre_surge` | recommend2 主升前夜 | 技术面短线 |

UNIQUE 约束：`(snapshot_date, ts_code, run_mode, source)` —— 同一日同一票不同策略各自一行。
UI / 推送 / 追踪可基于 source 字段过滤展示。

## 调用约定

任何脚本要用 core 包，必须先 sys.path bootstrap：

```python
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from core.db.connection import get_db
```

工作流统一用 `python -m core.<module>` 形式调用 core 内的入口脚本。

## 已完成

- [x] Phase 1 — 目录骨架 + 归档死代码
- [x] Phase 2 — zuiyou1 生态迁入 strategies/overnight_8step/
- [x] Phase 3 — autorecommend 核心迁入 strategies/llm_multisource/
- [x] Phase 4 — core/ 共享层落地（db/notify/tracker/market_data/ui/utils）
- [x] Phase 5 — daily_candidates 加 source 字段，zuiyou1 + aggregate 双写共表
- [x] Phase 6 — recommend2 并入 strategies/llm_multisource/pre_surge/
