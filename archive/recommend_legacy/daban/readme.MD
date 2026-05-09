# 短线打板策略 V2.0 - 混合数据源版部署手册

## 📁 文件
- `dabang_strategy_v2.py` (1193行,单文件可运行)

## 📦 依赖安装
```bash
pip install akshare baostock pandas numpy --break-system-packages
```

## 🎯 数据源分工策略

```
┌────────────────────┬──────────────┬──────────────┐
│ 数据类型           │ 主数据源     │ 备用数据源   │
├────────────────────┼──────────────┼──────────────┤
│ 涨停板池           │ AKShare ★   │ K线推算(降级)│
│ 龙虎榜             │ AKShare ★   │ 无可替代     │
│ 炸板池             │ AKShare ★   │ 无可替代     │
│ 全市场快照         │ AKShare ★   │ Baostock循环 │
│ 5分钟K线(9:50反包) │ Baostock ★  │ AKShare      │
│ 历史日K线          │ Baostock ★  │ AKShare      │
│ 股票基础信息       │ Baostock ★  │ AKShare      │
│ 交易日历           │ Baostock ★  │ AKShare      │
└────────────────────┴──────────────┴──────────────┘
```

★ = 优先源,失败自动切换备用

## 🔧 数据源开关 (Config类)
```python
USE_AKSHARE = True              # 启用AKShare
USE_BAOSTOCK = True             # 启用Baostock
PREFER_BAOSTOCK_FOR_KLINE = True  # K线优先用Baostock
```

**特别说明**:
- 即使AKShare被403封锁,核心打板数据(涨停池/龙虎榜)只能等接口恢复,**没有完全等价的替代源**
- K线数据通过Baostock兜底,不会受AKShare网络问题影响
- Baostock官方保持长期免费、稳定,login/logout无token限制

## 🚀 运行方式

### 1. 默认 (今日数据)
```bash
python dabang_strategy_v2.py
```

### 2. 指定日期 (在main函数改)
```python
result = strategy.run(target_date='20260505')
```

### 3. 集成OpenClaw定时
```bash
# 每个交易日18:30运行 (龙虎榜约18:00更新)
30 18 * * 1-5 cd /path && python dabang_strategy_v2.py >> dabang_cron.log 2>&1
```

## ⚙️ V2 vs V1 对比

| 特性 | V1 (纯AKShare) | V2 (混合) |
|------|---------------|-----------|
| K线数据稳定性 | 受AKShare影响 | Baostock兜底,稳定 |
| 5分钟K线性能 | AKShare较慢 | Baostock快3-5x |
| 涨停池兜底 | 无 | K线推算(精度低) |
| 全市场快照兜底 | 无 | Baostock循环(慢但可用) |
| 代码行数 | 959 | 1193 |
| 单元测试 | ✅ | ✅ |

## ⚠️ 已知边界

1. **境外IP仍无法替代龙虎榜数据** - Baostock没有此接口,这是A股数据的客观限制
2. **Baostock兜底快照很慢** - 全市场需要5000+次调用,实际只取前500-1000只股票,有数据偏差
3. **K线推算涨停板精度低** - 单日K线无法获取真实连板数,只能识别"今日涨停"
4. **Baostock登录会话** - 单例管理,程序退出自动logout,长时间运行需处理重连

## 📊 输出
- `/home/claude/dabang_reports/dabang_候选池_YYYYMMDD.csv`
- `/home/claude/dabang_reports/dabang_候选池_YYYYMMDD.md`
- `/home/claude/dabang_reports/dabang_YYYYMMDD.log`

## 🔍 故障排查

### Baostock登录失败
```bash
# 检查网络
curl -I http://baostock.com
# 检查防火墙
ping bao.qiyux.com
```

### AKShare 403
- 在境内VM运行 (东方财富对境外IP有限制)
- 或使用proxy代理(在AKShareProvider中配置)

### 涨停池为空但市场正常
- AKShare的`stock_zt_pool_em`接口偶发故障
- V2会自动尝试K线推算,但精度较低
- 建议次日重跑或手动指定上一交易日

## 🎓 与隔夜施工法搭配

| 维度 | 隔夜施工法 | 短线打板V2 |
|------|-----------|-----------|
| 持仓 | 3-5天波段 | 1-2天打板 |
| 信号 | 11层多因子 | 5层情绪流 |
| 数据 | AKShare | AKShare+Baostock |
| 风控 | 2/30/70 | 3/20/50 |

**资金分配建议**: 隔夜施工法60% + 短线打板40%