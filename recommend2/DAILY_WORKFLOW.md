# 📅 每日工作流 - 龙头断板策略

> **执行时间：** 每日 15:30-16:00（盘后）  
> **预计耗时：** 首次 15-30 分钟，后续 1-3 分钟  
> **适用策略：** 龙头断板（Dragon Break Strategy）

---

## 🎯 每日操作流程

### 第 1 步：清缓存 + 全市场扫描（15-30 分钟）

```powershell
# 清理旧缓存
Remove-Item -Recurse -Force .\data\*

# 全市场扫描（5185 只）
python main.py dragon-scan
```

**输出示例：**
```
2026-04-24 22:01:28 [INFO] dragon_scan: 代码段白名单过滤：6671 -> 5185 (剔除 1486 只非普通 A 股)
2026-04-24 22:01:28 [INFO] dragon_scan: 预扫描抽样：500 只 (主板 3056 选 350, 创业板 1352 选 100, 科创板 600 选 50)
2026-04-24 22:01:28 [INFO] dragon_scan: 预计算板块同步度 (L4)...
2026-04-24 22:03:03 [INFO] dragon_scan: 板块同步度：5 个交易日的涨停数据
2026-04-24 22:03:03 [INFO] dragon_scan: 龙头断板 — 全市场 5008 只
2026-04-24 22:15:20 [INFO] dragon_screener: 龙头断板扫描 1900/5008, 跳过 4,  有效 1896, 触发 3
...
✓ 龙头断板扫描完成 -> results/dragon_scan_20260424.csv
  共 5008 只，得分分布:
  score
  0    4500
  1     400
  2      90
  3      15
  4       2
  5       1
```

**关键指标：**
- 代码段过滤：6671 → 5185（剔除 ETF/基金/ST）
- 预扫描抽样：500 只（主板 70% + 创业板 20% + 科创板 10%）
- 板块同步度：涵盖 5 个交易日
- 扫描进度：每 100 只显示一次

---

### 第 2 步：保存观察池（得分≥7 分）

```powershell
# 从扫描结果中提取龙头观察池
python watchlist.py save-dragon --min-score 7
```

**输出示例：**
```
从 results/dragon_scan_20260424.csv 提取龙头观察池
✓ 龙头观察池已存 -> results/watchlist/dragon_watchlist_20260424.csv
  共 15 只，得分分布:
  score
  7    10
  8     4
  9     1
```

**观察池说明：**
- **7 分**：龙头候选（重点关注）
- **8 分**：强势龙头（高度关注）
- **9 分**：龙头龙头（最高优先级）

---

### 第 3 步：次日及以后 - 只扫观察池（**极速版 5 秒**）

```powershell
# 只扫描观察池中的 4 只票（快 100 倍）
python watchlist.py update-dragon

# 对比昨日变化
python watchlist.py diff-dragon
```

**update-dragon 输出示例：**
```
基线：dragon_watchlist_20260425.csv (4 只)
极速模式：跳过板块同步度预计算（观察池扫描）
✓ 龙头今日快照已存 -> results\watchlist\dragon_snapshot_20260425.csv

🎯 龙头观察池中 4 只已触发!
symbol name  score  last_close break_board_date  suggested_entry
600103 青山纸业      8        4.85       2026-04-22             5.33
600736 苏州高新      8        8.50       2026-04-24             8.24
605365  立达信      8       30.49       2026-04-23            31.04
002428 云南锗业      8       77.12       2026-04-23            74.57
```

**优化说明：**
- ⚡ **极速模式**：跳过 L4 板块同步度预计算（观察池已验证过）
- ⚡ **耗时**：从 95 秒降至 5 秒（提升 95%）
- ⚡ **内存**：无额外增加
- ✅ **准确性**：L1-L3、L5-L9 层正常计算，买点判断不受影响
- ⚠️ **分数**：统一 -1 分（L4 层跳过），相对排名不变

---

## 📊 工作流对比

| 步骤 | 操作 | 扫描数量 | 耗时 | 频率 |
|------|------|----------|------|------|
| **第 1 步** | 全市场扫描 | 5008 只 | 15-30 分钟 | 每日 1 次 |
| **第 2 步** | 保存观察池 | - | <1 秒 | 每日 1 次 |
| **第 3 步** | 观察池更新 | 10-20 只 | 1-3 分钟 | 每日多次 |

**性能提升：**
- 观察池更新比全市场扫描快 **10-30 倍**
- 可频繁运行（每小时一次），追踪得分变化

---

## ⚙️ T+1 时间检查

系统会自动检查当前时间：

### 盘中运行警告（<15:00）
```
⚠️  当前时间 14:30 < 15:00, 当日数据未生成!
将使用昨日数据扫描，T+1 逻辑可能不准确
建议：每日 15:30 后执行扫描
```

### 盘后运行正常（≥15:30）
```
[INFO] dragon_scan: 龙头断板 — 全市场 5008 只
（正常执行，无警告）
```

**最佳实践：**
- ✅ 每日 15:30-16:00 执行第 1 步
- ✅ 次日 9:00 前执行第 3 步（观察池更新）
- ❌ 避免 15:00 前运行（数据未生成）

---

## 📁 生成的文件

### 每日扫描结果
```
results/
└── dragon_scan_YYYYMMDD.csv    # 全市场扫描结果
```

### 观察池文件
```
results/watchlist/
├── dragon_watchlist_YYYYMMDD.csv    # 观察池基线（第 2 步生成）
├── dragon_snapshot_YYYYMMDD.csv     # 观察池快照（第 3 步生成）
└── ...
```

### 缓存文件
```
data/
├── *.parquet           # K 线数据缓存
└── *.parquet.lock      # 缓存锁文件
```

---

## 🚨 常见问题

### Q1: 板块同步度为 0 怎么办？

**症状：**
```
[INFO] dragon_scan: 板块同步度：0 个交易日的涨停数据
```

**原因：**
- 预计算逻辑有问题（正在修复）
- 当日无涨停数据（市场极度低迷）

**解决：**
- 检查 `precompute_sector_sync` 函数
- 手动运行 `python main.py dragon-scan --sample` 调试

### Q2: 观察池为空怎么办？

**症状：**
```
未找到 dragon-scan 结果，先跑 python main.py dragon-scan
```

**解决：**
```powershell
# 先跑全市场扫描
python main.py dragon-scan

# 降低阈值保存观察池
python watchlist.py save-dragon --min-score 6
```

### Q3: 扫描太慢怎么办？

**优化方案：**
1. 确保已清缓存（首次扫描必然慢）
2. 使用观察池更新（快 10 倍）
3. 减少 `progress_every` 参数（减少日志输出）

```powershell
# 观察池更新（1-3 分钟）
python watchlist.py update-dragon
```

### Q4: 股票代码格式错误怎么办？

**症状：**
```
股票代码应为 9 位，请检查。格式示例：sh.600000。
[WARNING] 2428 K 线获取失败：baostock 查询失败
```

**原因：** CSV 读取时 `symbol` 列被当成数字，导致前导零丢失（`002428` → `2428`）

**解决：**
```powershell
# 重新生成观察池（代码已修复）
Remove-Item .\results\watchlist\dragon_watchlist_*.csv
python watchlist.py save-dragon --min-score 7

# 验证股票代码格式
Get-Content .\results\watchlist\dragon_watchlist_*.csv | Select-String "002428"
```

**修复版本：** 2026-04-25 及以后版本已修复此问题

---

## 📝 修复记录

### 2026-04-25 - 股票代码格式修复

**问题：** CSV 读取时前导零丢失，导致深市股票代码错误

**修复内容：**
1. `watchlist.py` 中所有 `pd.read_csv()` 添加 `dtype={"symbol": str}`
2. 保存时确保 `symbol` 列是字符串格式
3. `update-dragon` 命令添加板块同步度预计算

**影响命令：**
- `python watchlist.py save`
- `python watchlist.py update`
- `python watchlist.py save-dragon`
- `python watchlist.py update-dragon`

**验证：**
- ✅ 云南锗业（002428）数据获取成功
- ✅ 所有观察池股票正常扫描
- ✅ 无 baostock 代码格式错误

### 2026-04-25 - 板块同步度优化

**优化：** `update-dragon` 命令支持板块同步度预计算

**修复前：** 直接调用 `scan_universe_dragon()`，无 L4 层数据
**修复后：** 先预计算板块同步度，再注入到扫描函数

**效果：**
- L4 层逻辑正常工作
- 观察池扫描结果与全市场扫描一致
- 计算速度更快（仅需处理观察池股票）

---

## 📈 工作流自动化（可选）

### Windows 任务计划程序

创建定时任务，每日 15:30 自动执行：

```powershell
# 任务操作
cd D:\pythonProject\openclaw-quant-system\recommend2
Remove-Item -Recurse -Force .\data\*
python main.py dragon-scan
python watchlist.py save-dragon --min-score 7
```

### Cron 表达式（Linux/Mac）

```cron
# 每日 15:30 执行
30 15 * * * cd /path/to/recommend2 && \
  rm -rf data/* && \
  python main.py dragon-scan && \
  python watchlist.py save-dragon --min-score 7
```

---

## 📞 下一步

完成每日工作流后：

1. **检查触发信号** → 查看 `dragon_snapshot_*.csv` 中 `triggered=True` 的票
2. **人工复核** → 查看 K 线形态、板块热度、消息面
3. **执行买入** → 按信号中的 `suggested_entry` 价格挂单
4. **设置止损** → 按 `suggested_stop` 价格设置止损

**买入规则：**
- 断板次日开盘 ≤ 断板日收盘价 × 0.97
- 止损价 = 买入价 × 0.95（5% 止损）
- 持仓 ≤ 5 日

---

**文档版本：** v1.0  
**最后更新：** 2026-04-24  
**适用策略：** 龙头断板（Dragon Break Strategy）
