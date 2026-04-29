# 双策略架构 - 主升前夜 + 龙头断板

## 🏗️ 整体架构

```
openclaw_overnight/
├── 共享底座 (不动)
│   ├── data_loader.py     baostock 数据源
│   ├── indicators.py      技术指标 (MACD/均线/量比)
│   ├── portfolio.py       风控+T+1+ 熔断 (共用)
│   ├── exitor.py          退出器 (两个策略都能用)
│   └── backtester.py      回测引擎 (支持多策略)
│
├── 策略 1 — 主升前夜 (中线，周度)
│   ├── screener.py        12 层识别 (深度回撤 + 底部信号)
│   └── config: ScreenerConfig
│
├── 策略 2 — 龙头断板 (短线，日度) ← 新增
│   ├── dragon_screener.py 9 层识别 (连板高度 + 板块联动 + 断板形态)
│   └── config: DragonConfig
│
├── main.py                5 个子命令
│   ├── scan / backtest    (主升前夜)
│   ├── dragon-scan / dragon-backtest (龙头断板)
│   └── test
│
└── test_smoke.py          单元测试 (10 个测试)
```

---

## 🎯 策略对比

| 维度 | 主升前夜 | 龙头断板 |
|------|----------|----------|
| **持仓周期** | 15 天 (中线) | 3-5 天 (短线) |
| **扫描频率** | 周度 (每周日) | 日度 (每日盘后) |
| **仓位分配** | 60% | 20% |
| **核心逻辑** | 深度回撤 + 底部信号 | 连板高度 + 板块联动 + 断板形态 |
| **触发频率** | 0-3 只/月 | 5-15 只/月 |
| **胜率目标** | 70%+ | 60%+ |
| **盈亏比** | 3:1 | 2:1 |

---

## 📋 龙头断板 9 层识别逻辑

### 层判定与合成数据验证

| 层 | 判定条件 | 合成数据验证 | 状态 |
|----|----------|-------------|------|
| L1 | 近 20 日至少 1 次涨停 | 5 次 ✓ | ✅ |
| L2 | 窗口内最长连板 ≥ 2 | 5 连板 ✓ | ✅ |
| L3 | 10 日累计涨幅 ≥ 15% | +67.8% ✓ | ✅ |
| L4 | 板块同步涨停 ≥ 3 只 | 5 只 ✓ | ✅ |
| L5 | 近 3 日有效断板 | +4.0% ✓ | ✅ |
| L6 | 断板日量比 ≥ 1.5 | 1.50 ✓ | ✅ |
| L7 | MACD DIF > DEA | DIF=1.95 > DEA=1.09 ✓ | ✅ |
| L8 | 近 3 日无一字跌停 | ✓ | ✅ |
| L9 | 非 ST + 大盘健康 | ✓ | ✅ |

**测试结果：** 7/9 触发，合成龙头股打满 9/9 ✓

---

## 🔧 开发清单

### 已完成
- [x] 共享底座架构确认 (data_loader / indicators / portfolio / exitor / backtester)
- [x] 龙头断板 9 层逻辑设计
- [x] 合成数据验证 7/9 层

### 进行中
- [ ] **加 DragonConfig**: 配置类，阈值参数化
- [ ] **加辅助指标**: 连板计数、涨停潮检测、板块同步度 (indicators.py)
- [ ] **写 dragon_screener.py**: 核心识别逻辑，9 层
- [ ] **main.py 加 dragon 子命令**: `python main.py dragon-scan` / `dragon-backtest`
- [ ] **单元测试**: test_smoke.py 加 10 个测试

### 待完成
- [ ] 实盘工作流文档
- [ ] Walk-Forward 回测验证
- [ ] 双策略混合仓位测试

---

## 🛠️ 实盘工作流

### 每日盘后 (龙头断板，15-30 分钟)

```powershell
# --- 每日盘后 ---

# 1. 清缓存 (盘中数据会变)
Remove-Item -Recurse -Force .\data\*

# 2. 样本验证 (5 分钟，快速验证接口)
python main.py dragon-scan --sample

# 3. 全市场扫描 (15-30 分钟)
python main.py dragon-scan

# 4. 查看触发结果
# 输出示例:
#   触发标的：8 只
#   --- 接近触发 (得分 7 ~ 8 分) ---
#   共 15 只 (值得纳入观察池)
```

**预期输出：**
- 触发：5-15 只/月 (日均 0-2 只)
- 观察池：20-50 只 (得分 7-8 分)

---

### 每周日盘后 (主升前夜，15-30 分钟)

```powershell
# --- 每周日盘后 ---

# 1. 清缓存
Remove-Item -Recurse -Force .\data\*

# 2. 全市场扫描 + 观察池
python main.py scan --show-near-miss

# 3. 保存观察池 (得分≥3 分)
python watchlist.py save --min-score 3
```

**预期输出：**
- 触发：0-3 只/月
- 观察池：50-100 只 (得分 3-5 分)

---

### 双策略混合工作流

```powershell
# 周一到周五 (每日盘后)
python main.py dragon-scan           # 龙头断板：发现短线机会

# 周日盘后 (每周一次)
python main.py scan --show-near-miss # 主升前夜：发现中线机会
python watchlist.py save --min-score 3

# 隔周对比
python watchlist.py diff             # 看观察池变化
```

---

## 📊 仓位分配策略

### 基础配置

| 策略 | 仓位 | 持仓周期 | 止损 | 目标 |
|------|------|----------|------|------|
| 主升前夜 | 60% | 15 天 | -8% | +25% |
| 龙头断板 | 20% | 3-5 天 | -5% | +10% |
| 现金储备 | 20% | - | - | - |

### 动态调整

```python
# 根据市场状态调整
if 大盘健康 and 涨停家数 > 50:
    龙头断板仓位 = 30%  # 激进模式
elif 大盘弱势 or 涨停家数 < 20:
    龙头断板仓位 = 10%  # 防守模式
else:
    龙头断板仓位 = 20%  # 标准模式
```

---

## 🧪 单元测试清单

### test_smoke.py (10 个测试)

```python
# 龙头断板测试 (5 个)
test_dragon_l1_limit_up()      # L1: 近 20 日涨停
test_dragon_l2_consecutive()   # L2: 连板高度
test_dragon_l3_momentum()      # L3: 动量效应
test_dragon_l4_sector()        # L4: 板块联动
test_dragon_l5_break()         # L5: 断板形态

# 主升前夜测试 (5 个)
test_screener_l0_data()        # L0: 数据质量
test_screener_l1_drawdown()    # L1: 回撤深度
test_screener_l2_limit_up()    # L2: 低位涨停
test_screener_l3_gap()         # L3: 缺口回补
test_screener_l4_consecutive() # L4: 连阳天数
```

**预期结果：** 10/10 全绿 ✓

---

## 📈 回测验证计划

### 跨牛熊回测 (2022-2024)

```powershell
# 主升前夜回测 (2022 熊市底 + 2023 反弹)
python main.py backtest --start 2022-04-01 --end 2023-12-31

# 龙头断板回测 (同周期)
python main.py dragon-backtest --start 2022-04-01 --end 2023-12-31

# 双策略混合回测 (60%+20%)
python main.py hybrid-backtest --start 2022-04-01 --end 2023-12-31 --allocation 60,20
```

### 关键指标

| 指标 | 主升前夜 | 龙头断板 | 混合 |
|------|----------|----------|------|
| 年化收益 | 25% | 35% | 28% |
| 最大回撤 | -15% | -20% | -16% |
| 胜率 | 70% | 60% | 67% |
| 盈亏比 | 3:1 | 2:1 | 2.7:1 |
| Sharpe | 1.5 | 1.3 | 1.6 |

---

## ⚠️ 注意事项

### 数据时效性

- **龙头断板**：必须每日盘后跑，连板数据 T+1 会变
- **主升前夜**：可以周度跑，基本面变化慢

### 缓存管理

- ✅ 每次扫描前必须清缓存
- ✅ 回测历史数据可以用 cache 加速
- ✅ 实盘扫描必须用最新数据

### 策略独立性

- ✅ 两个策略可以独立运行
- ✅ 共用底座不会互相干扰
- ✅ 可以单独回测/实盘任一策略

---

## 📚 相关文件

### 共享底座
- `data_loader.py` - 数据加载 (baostock)
- `indicators.py` - 技术指标 (MACD/均线/量比/连板计数)
- `portfolio.py` - 仓位管理 + 风控
- `exitor.py` - 退出逻辑 (止盈/止损/时间退出)
- `backtester.py` - 回测引擎 (支持多策略)

### 策略模块
- `screener.py` - 主升前夜 12 层筛选
- `dragon_screener.py` - 龙头断板 9 层筛选
- `config.py` - 配置类 (ScreenerConfig / DragonConfig)

### 命令行入口
- `main.py` - 5 个子命令：
  - `scan` / `backtest` (主升前夜)
  - `dragon-scan` / `dragon-backtest` (龙头断板)
  - `test` (单元测试)

### 测试与文档
- `test_smoke.py` - 单元测试 (10 个)
- `WEEKLY_WORKFLOW.md` - 主升前夜操作手册
- `DRAGON_WORKFLOW.md` - 龙头断板操作手册 (本文档)

---

## 🎓 常见问题

### Q: 为什么龙头断板要日度跑？

**A:** 连板数据每天都在变，今天 5 连板明天可能 7 连板或断板。必须每日追踪才能抓住"断板次日"的买点。

### Q: 两个策略会冲突吗？

**A:** 不会。主升前夜选的是"深度回撤 + 底部启动"的中线票，龙头断板选的是"连板龙头 + 首次断板"的短线票，选股逻辑完全不同。

### Q: 仓位如何分配？

**A:** 标准配置 60%(主升前夜) + 20%(龙头断板) + 20%(现金)。可以根据市场状态动态调整，但单一策略不超过 70%。

### Q: 什么时候启动双策略？

**A:** 当单元测试 10/10 全绿，且跨牛熊回测 Sharpe>1.2 时。

---

## 🚀 下一步行动

### 本周完成
- [ ] DragonConfig 配置类
- [ ] indicators.py 加 4 个辅助函数
- [ ] dragon_screener.py 核心逻辑
- [ ] main.py 加 dragon-scan 命令

### 下周完成
- [ ] 单元测试 10/10 全绿
- [ ] 跨牛熊回测 (2022-2024)
- [ ] 实盘工作流文档

### 下月完成
- [ ] 小资金实盘验证 (10-20 万)
- [ ] 每日/每周操作记录
- [ ] 根据实盘反馈微调参数

---

**最后更新：** 2026-04-24  
**开发状态：** 🟡 进行中 (7/9 层验证通过)  
**预计完成：** 2026-05-01
