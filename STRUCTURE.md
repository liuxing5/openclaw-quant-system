# 项目结构（重构进行中）

## 三层架构

```
strategies/                  策略层（互相独立）
├── overnight_8step/         ⭐ zuiyou1 隔夜8步法（生产核心）
└── llm_multisource/         autorecommend 多源LLM策略（含 recommend2 主升前夜）

platform/                    共享基础设施（两边复用）
├── db/                      PostgreSQL schema + 连接 + 候选池读写
├── notify/                  Telegram 推送（统一接口）
├── tracker/                 T+N 收益追踪
├── market_data/             akshare/baostock/腾讯接口
└── ui/                      Streamlit Web 看板

frameworks/quant_system/     量化框架库（保留，未来再用）

archive/                     历史代码（只读归档）
├── recommend_legacy/        recommend/ 顶层旧脚本
└── xuangu_experiments/      xuangu/ 下实验性脚本

scripts/                     一次性脚本
docs/                        各类报告/笔记
tests/                       单元/集成测试
```

## 候选池统一表（合并核心）

`daily_candidates` 表加 `source` 字段，三种来源汇入同一张表：

| source | 来源策略 | 方法论 |
|---|---|---|
| `overnight_8step` | zuiyou1 | 量价规则引擎 |
| `llm_multisource` | autorecommend collector + extractor | LLM 多源分析 |
| `pre_surge` | recommend2 主升前夜 | 技术面短线 |

UI / 推送 / 追踪基于 source 字段统一处理。

## 重构阶段

- [x] Phase 1a — 创建目录骨架
- [ ] Phase 1b — 归档 recommend/ 顶层死代码
- [ ] Phase 1c — 归档 xuangu/ 实验代码
- [ ] Phase 2 — 移动 zuiyou1 生态到 strategies/overnight_8step/
- [ ] Phase 3 — 移动 autorecommend 核心到 strategies/llm_multisource/
- [ ] Phase 4 — 抽取 platform/ 共享层
- [ ] Phase 5 — recommend2 合并进 strategies/llm_multisource/pre_surge/
