# 龙头断板 - 实盘操作手册

## 📅 每日盘后工作流 (15-30 分钟)

```powershell
# 1. 清缓存 (盘中数据会变)
Remove-Item -Recurse -Force .\data\*

# 2. 样本验证 (5 分钟，快速验证接口)
python main.py dragon-scan --sample

# 3. 全市场扫描 (15-30 分钟)
python main.py dragon-scan
```

### 预期输出

```
触发标的：8 只
  603171 税友股份    L1-L9 全通过
  603939 益丰药房    L1-L9 全通过
  ...

--- 接近触发 (得分 7 ~ 8 分) ---
共 15 只 (值得纳入观察池)
  002439 启明星辰    8 分  L9: 大盘不健康
  600052 东望时代    7 分  L6: 量比仅 1.3
```

---

## 📆 每周日盘后工作流 (主升前夜)

```powershell
# 1. 清缓存
Remove-Item -Recurse -Force .\data\*

# 2. 全市场扫描 + 观察池
python main.py scan --show-near-miss

# 3. 保存观察池 (得分≥3 分)
python watchlist.py save --min-score 3
```

---

## 🎯 双策略对比

| 维度 | 主升前夜 | 龙头断板 |
|------|----------|----------|
| **频率** | 周度 | 日度 |
| **持仓** | 15 天 (中线) | 3-5 天 (短线) |
| **仓位** | 60% | 20% |
| **触发** | 0-3 只/月 | 5-15 只/月 |
| **胜率** | 70%+ | 60%+ |

---

## 📊 龙头断板 9 层识别

| 层 | 判定条件 | 状态 |
|----|----------|------|
| L1 | 近 20 日至少 1 次涨停 | ✅ |
| L2 | 窗口内最长连板 ≥ 2 | ✅ |
| L3 | 10 日累计涨幅 ≥ 15% | ✅ |
| L4 | 板块同步涨停 ≥ 3 只 | ✅ |
| L5 | 近 3 日有效断板 | ✅ |
| L6 | 断板日量比 ≥ 1.5 | ✅ |
| L7 | MACD DIF > DEA | ✅ |
| L8 | 近 3 日无一字跌停 | ✅ |
| L9 | 非 ST + 大盘健康 | ✅ |

**测试结果：** 7/9 触发，合成龙头股 9/9 ✓

---

## 🛠️ 常用命令

### 龙头断板
```powershell
# 样本扫描 (5 分钟)
python main.py dragon-scan --sample

# 全市场扫描 (15-30 分钟)
python main.py dragon-scan

# 回测 (2022-2024)
python main.py dragon-backtest --start 2022-04-01 --end 2024-12-31
```

### 主升前夜
```powershell
# 样本扫描
python main.py scan --sample

# 全市场扫描 + 观察池
python main.py scan --show-near-miss

# 保存观察池
python watchlist.py save --min-score 3

# 回测
python main.py backtest --start 2024-06-01 --end 2024-12-31
```

---

## ⚠️ 注意事项

### 必须清缓存的场景
- ✅ 每日盘后扫描 (数据会变)
- ✅ 每周日盘后扫描
- ✅ 策略代码修改后

### 可以不清缓存的场景
- ✅ 回测历史数据 (用 cache 加速)
- ✅ 测试命令 (test)

---

## 📝 操作检查清单

### 每日必做 (周一到周五)
- [ ] 清缓存 `Remove-Item -Recurse -Force .\data\*`
- [ ] 样本验证 `python main.py dragon-scan --sample`
- [ ] 全市场扫描 `python main.py dragon-scan`
- [ ] 记录触发数量 (预期 0-2 只)
- [ ] 记录观察池变化 (预期 20-50 只)

### 每周必做 (周日)
- [ ] 清缓存
- [ ] 主升前夜扫描 `python main.py scan --show-near-miss`
- [ ] 保存观察池 `python watchlist.py save --min-score 3`
- [ ] 记录触发数量 (预期 0 只)
- [ ] 记录最高得分 (预期 3-5 分)

---

## 🎓 常见问题

### Q: 龙头断板为什么每天跑？
**A:** 连板数据每天都在变，必须每日追踪才能抓住"断板次日"的买点。

### Q: 两个策略会冲突吗？
**A:** 不会。选股逻辑完全不同，可以独立运行。

### Q: 仓位如何分配？
**A:** 60%(主升前夜) + 20%(龙头断板) + 20%(现金)。

---

## 📚 相关文件

- `main.py` - 主程序入口
- `dragon_screener.py` - 龙头断板筛选器
- `screener.py` - 主升前夜筛选器
- `config.py` - 策略配置
- `WEEKLY_WORKFLOW.md` - 主升前夜详细手册
- `DRAGON_WORKFLOW.md` - 双策略架构文档

---

**最后更新：** 2026-04-24  
**策略状态：** 🟡 开发中 (7/9 层验证通过)  
**预计完成：** 2026-05-01
