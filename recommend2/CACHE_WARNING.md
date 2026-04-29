# ⚠️ 重要警告 - 缓存管理

## 🎯 快速参考 - 新工作流

### 龙头断板策略 (每日盘后)

```powershell
# 第 1 步：清缓存 + 全市场扫描 (15-30 分钟)
Remove-Item -Recurse -Force .\data\*
python main.py dragon-scan

# 第 2 步：保存观察池 (得分≥7 分)
python watchlist.py save-dragon --min-score 7

# 第 3 步：次日及以后只扫观察池 (1-3 分钟，快 10 倍)
python watchlist.py update-dragon
python watchlist.py diff-dragon
```

### 主升前夜策略 (每周日盘后)

```powershell
# 第 1 步：清缓存 + 全市场扫描 (15-30 分钟)
Remove-Item -Recurse -Force .\data\*
python main.py scan --show-near-miss

# 第 2 步：保存观察池 (得分≥3 分)
python watchlist.py save --min-score 3

# 第 3 步：次日及以后只扫观察池 (1-3 分钟)
python watchlist.py update
python watchlist.py diff
```

### ⚙️ T+1 时间检查

系统会在 15:00 前运行时自动警告：
```
⚠️  当前时间 14:30 < 15:00, 当日数据未生成!
将使用昨日数据扫描，T+1 逻辑可能不准确
建议：每日 15:30 后执行扫描
```

**最佳执行时间：** 每日 15:30-16:00

---

## 🔴 必须清缓存的场景

### 1. 每日/每周扫描前

**龙头断板 (每日盘后)**
```powershell
# 必须清缓存！盘中数据会变
Remove-Item -Recurse -Force .\data\*
python main.py dragon-scan
```

**主升前夜 (每周日)**
```powershell
# 必须清缓存！确保数据最新
Remove-Item -Recurse -Force .\data\*
python main.py scan --show-near-miss
```

### 2. 策略代码修改后

```powershell
# 修改了 screener.py / dragon_screener.py / indicators.py 后
Remove-Item -Recurse -Force .\data\*
python main.py test
```

### 3. 发现数据异常时

```powershell
# 如果扫描结果与预期不符，先清缓存再跑
Remove-Item -Recurse -Force .\data\*
python main.py dragon-scan --sample
```

---

## ✅ 可以不清缓存的场景

### 1. 回测历史数据

```powershell
# 回测用历史数据，可以用 cache 加速
python main.py backtest --start 2024-01-01 --end 2024-12-31
```

### 2. 测试命令

```powershell
# 单元测试不需要清缓存
python main.py test
```

### 3. 观察池更新 (已实现缓存复用)

```powershell
# watchlist.py update 会自动复用缓存
python watchlist.py update-dragon
```

---

## 🕐 智能缓存 TTL 机制

系统已实现智能缓存过期策略：

| 时段 | TTL | 说明 |
|------|-----|------|
| **盘中** (9:30-15:30) | 2 小时 | 快速刷新，适应盘中变化 |
| **盘后** (15:30-次日 9:00) | 24 小时 | 隔夜有效，避免重复拉取 |

**自动切换逻辑：**
- 9:30-15:30 → 缓存 2 小时后过期
- 15:30-次日 9:00 → 缓存 24 小时后过期

**注意：** 即使有智能 TTL，**扫描前仍建议手动清缓存**，确保使用最新数据！

---

## 📁 缓存位置

```
recommend2/
└── data/
    ├── *.parquet        # K 线数据缓存
    ├── *.parquet.lock   # 缓存锁文件
    └── ...
```

---

## 🛠️ 清缓存命令

### Windows PowerShell
```powershell
Remove-Item -Recurse -Force .\data\*
```

### Linux / macOS
```bash
rm -rf data/*
```

### 验证缓存已清理
```powershell
# PowerShell
Get-ChildItem .\data\

# 应该显示空目录或只有锁文件
```

---

## ⚙️ T+1 时间检查

系统会在扫描前检查当前时间：

```
⚠️  当前时间 14:30 < 15:00, 当日数据未生成!
将使用昨日数据扫描，T+1 逻辑可能不准确
建议：每日 15:30 后执行扫描
```

**最佳实践：**
- 龙头断板：每日 15:30-16:00 执行
- 主升前夜：每周日 15:30-16:00 执行

---

## 🚨 常见错误

### 错误 1：忘记清缓存

```
症状：扫描结果与昨日相同
解决：Remove-Item -Recurse -Force .\data\*
```

### 错误 2：盘中跑扫描

```
症状：T+1 逻辑混乱，买入信号错误
解决：15:30 后再跑，或手动指定 end_date
```

### 错误 3：缓存文件损坏

```
症状：parquet 读取失败
解决：Remove-Item -Recurse -Force .\data\*
```

---

## 📊 缓存命中率

**典型场景：**
- 全市场扫描 (6494 只)：首次 15-30 分钟，后续 5-10 分钟 (有缓存)
- 观察池更新 (50-100 只)：1-3 分钟 (复用缓存)
- 龙头断板扫描 (6494 只)：首次 15-30 分钟，后续 5-10 分钟 (有缓存)

**优化建议：**
- 每日扫描：清缓存 → 15-30 分钟
- 观察池更新：不清缓存 → 1-3 分钟

---

**最后更新：** 2026-04-24  
**适用版本：** v2.0+
