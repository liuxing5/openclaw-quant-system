# 5策略共振技术指标过滤模块

## 概述

基于逻辑契合度最优的5策略共振过滤系统，专为**隔夜八步法**和**LLM多源策略**设计。

### 核心策略（必选）

| 策略 | 逻辑 | 契合原因 |
|------|------|---------|
| **20周线保命法** | 收盘价站上20周均线（约100日线） | 与八步法第6步完全契合，中期趋势向上 |
| **均线多头排列** | 5日>10日>20日，股价在所有均线上方 | 八步法"无套牢盘"的本质就是均线多头 |
| **MACD金叉** | 零轴上方金叉，红柱持续放大 | 完美验证八步法的"量价强势"是否真实 |

### 增强策略（强烈推荐）

| 策略 | 逻辑 | 契合原因 |
|------|------|---------|
| **布林上轨追涨** | 刚突破上轨且量比>1.5 | 与涨幅区间互补，看相对位置而非绝对值 |
| **年线定海神针** | 收盘价站上250日均线 | 底层趋势过滤，避免下降趋势中做隔夜 |

## 文件结构

```
strategies/resonance_filters/
├── __init__.py              # 模块初始化
├── technical_filters.py     # 技术指标过滤核心实现
└── resonance_config.py      # 配置文件（默认/保守/激进）

strategies/
├── combine_strategies.py    # 三层整合策略（共振+LLM+八步法）
└── run_resonance_strategy.py # 实盘运行入口脚本
```

## 快速开始

### 1. 完整三层筛选（推荐）

```bash
# 从数据库加载LLM候选
python strategies/run_resonance_strategy.py --llm-db

# 从CSV文件加载LLM候选
python strategies/run_resonance_strategy.py --llm-csv ./llm_results.csv
```

### 2. 仅共振+八步法（跳过LLM层）

```bash
python strategies/run_resonance_strategy.py
```

### 3. 指定交易日期

```bash
python strategies/run_resonance_strategy.py --date 2026-05-12
```

### 4. 调整过滤参数

```bash
# 禁用年线和布林带
python strategies/run_resonance_strategy.py --no-annual --no-bollinger

# 降低通过策略数量要求
python strategies/run_resonance_strategy.py --min-pass 3

# 不要求核心3策略必须通过
python strategies/run_resonance_strategy.py --no-core
```

### 5. 单独运行技术指标过滤

```bash
# 过滤全市场
python strategies/resonance_filters/technical_filters.py

# 指定日期和输出
python strategies/resonance_filters/technical_filters.py --date 2026-05-12 -o results.csv

# 禁用某些策略
python strategies/resonance_filters/technical_filters.py --no-annual --no-bollinger
```

## 输出格式

### CSV输出字段

| 字段 | 说明 |
|------|------|
| code | 股票代码 |
| name | 股票名称 |
| pct | 涨幅% |
| vol_ratio | 量比 |
| turn | 换手率% |
| score | 八步法评分 |
| tags | 标签 |
| industry | 行业 |
| ma_20week | 20周线是否通过 |
| ma_bullish | 均线多头是否通过 |
| macd | MACD是否通过 |
| bollinger | 布林上轨是否通过 |
| annual_line | 年线是否通过 |
| passed_count | 通过的策略数量 |
| total_count | 总策略数量 |

## 配置选项

### 默认配置（DEFAULT_CONFIG）

- 最少通过：3个策略
- 核心策略：必须全部通过
- 增强策略：全部启用

### 保守配置（CONSERVATIVE_CONFIG）

- 最少通过：5个策略（全部）
- 核心策略：必须全部通过
- LLM最低分：30分
- 八步法评分：≥85分

### 激进配置（AGGRESSIVE_CONFIG）

- 最少通过：3个策略
- 核心策略：不要求全部通过
- 年线过滤：禁用
- LLM最低分：20分
- 八步法评分：≥75分

## 技术指标计算说明

### 20周线保命法

```python
# 计算100日移动平均线
ma_100 = close.rolling(100).mean()

# 判断趋势（比较今日和5日前的MA100）
ma_trend = 'up' if ma_100 > ma_100_5d_ago * 1.001 else 'down'

# 通过条件：收盘价 > MA100 且 趋势向上
passed = (close > ma_100) and (ma_trend == 'up')
```

### 均线多头排列

```python
# 计算3条均线
ma_5 = close.rolling(5).mean()
ma_10 = close.rolling(10).mean()
ma_20 = close.rolling(20).mean()

# 通过条件：均线顺序正确 + 股价在所有均线上方
passed = (ma_5 > ma_10 > ma_20) and (close > ma_5)
```

### MACD金叉

```python
# 计算MACD（12, 26, 9）
ema_fast = close.ewm(span=12).mean()
ema_slow = close.ewm(span=26).mean()
dif = ema_fast - ema_slow
dea = dif.ewm(span=9).mean()
hist = (dif - dea) * 2

# 通过条件：零轴上方 + （金叉或红柱放大）
passed = (dif > 0) and (dea > 0) and ((golden_cross) or (hist > 0 and hist > hist_prev))
```

### 布林上轨追涨

```python
# 计算布林带（20, 2）
middle = close.rolling(20).mean()
std = close.rolling(20).std()
upper = middle + (std * 2)

# 量比
volume_ratio = today_vol / avg_vol_20

# 通过条件：突破上轨 + 量比>1.5 + 非回踩
passed = (close > upper) and (volume_ratio > 1.5) and not is_pullback
```

### 年线定海神针

```python
# 计算250日移动平均线
ma_250 = close.rolling(250).mean()

# 通过条件：收盘价 > MA250
passed = close > ma_250
```

## 三层筛选架构

```
┌─────────────────────────────────────────────────┐
│  第1层：5策略共振过滤                              │
│  - 20周线保命法（中期趋势）                        │
│  - 均线多头排列（短中期趋势一致）                  │
│  - MACD金叉（动能验证）                           │
│  - 布林上轨追涨（突破确认）                        │
│  - 年线定海神针（底层趋势过滤）                    │
└────────────────┬────────────────────────────────┘
                 ▼
┌─────────────────────────────────────────────────┐
│  第2层：LLM多源策略                                │
│  - 新闻资讯分析                                   │
│  - 研报/公告/龙虎榜                               │
│  - 板块概念共振                                   │
└────────────────┬────────────────────────────────┘
                 ▼
┌─────────────────────────────────────────────────┐
│  第3层：隔夜八步法精选                             │
│  - 涨幅3-5%区间                                  │
│  - 量比1.5-10倍                                  │
│  - 换手率3-10%                                   │
│  - MA5贴线运行                                   │
│  - 评分≥80分                                     │
└────────────────┬────────────────────────────────┘
                 ▼
         🎯 最终候选池（Top 3-5）
```

## 止损铁律

- **稳健路径**：次日09:35未维持昨收+1%，直接出局
- **高位路径**：次日竞价弱于昨收，集合竞价结束即清仓
- **全局止损**：亏损超2.5%无条件止损

## 注意事项

1. **数据要求**：需要至少250个交易日的历史数据才能计算年线
2. **运行时间**：建议盘后15:10+运行，确保数据完整
3. **数据库依赖**：需要PostgreSQL数据库和daily_quotes表
4. **性能优化**：技术指标计算有缓存机制，批量查询时自动复用数据

## 依赖

- Python 3.8+
- pandas
- numpy
- psycopg2
- 项目core模块（db/connection.py）
