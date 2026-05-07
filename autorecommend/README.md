# AI Stock Recommendation System

基于 LLM 的 A 股智能推荐系统，通过 RSS 资讯采集、大模型信号提取、量化打分，每日生成候选股票池并推送到 Telegram。

## 架构

```
RSS/资讯源 → RSSHub → 采集器 → PostgreSQL → LLM 提取 → 策略聚合 → Telegram 推送
                                    ↓
                              AKShare 行情数据
```

## 部署

### GitHub Actions（推荐）

1. Fork 本仓库
2. 在 Settings → Secrets → Actions 中添加：
   - `LLM_API_KEY` - 主模型 API Key
   - `LLM_BASE_URL` - 主模型 API 地址
   - `LLM_MODEL` - 主模型名称
   - `DEEPSEEK_API_KEY` - 备用模型 API Key
   - `TELEGRAM_BOT_TOKEN` - Telegram Bot Token
   - `TELEGRAM_CHAT_ID` - 接收消息的 Chat ID
   - `POSTGRES_PASSWORD` - 数据库密码

3. 每天 15:30 (UTC+8) 自动运行，或手动触发

### 本地部署

见 `setup/` 目录下的安装脚本。

## 目录结构

```
├── .github/workflows/     # GitHub Actions 配置
├── collector/src/         # 数据采集（RSS + AKShare）
├── analyzer/src/          # LLM 信号提取
├── strategy/src/          # 策略聚合层
├── bot/src/               # Telegram 推送
├── configs/               # 数据库 Schema、feeds 配置
├── setup/                 # 部署脚本
└── requirements.txt       # Python 依赖
```

## 数据流

1. **采集**：RSSHub 获取财经资讯，AKShare 获取行情数据
2. **提取**：LLM 分析资讯，提取结构化股票推荐信号
3. **聚合**：综合 LLM 分数、量化指标、共识度生成候选池
4. **推送**：每日收盘后推送 Top 候选股到 Telegram

## 模型配置

- 主模型：Xiaomimimo (mimo-v2.5-pro)
- 备用模型：DeepSeek (deepseek-chat)
- 自动切换：主模型连续失败 3 次后自动切换备用
