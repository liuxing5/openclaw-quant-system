# AI Stock Recommender - 整体启动检查清单

在第一个完整交易日开跑前，按这个清单全过一遍：

| 项目 | 命令 | 预期 |
|------|------|------|
| RSSHub 在跑 | `docker compose ps` | 三个容器 Up |
| 雪球路由通 | `curl ".../xueqiu/user/...?key=..."` | 有内容 |
| Postgres 通 | `psql -c "SELECT 1"` | 1 |
| Redis 通 | `redis-cli ping` | PONG |
| 采集服务在跑 | `systemctl status stockrec-collector` | active |
| 提取服务在跑 | `systemctl status stockrec-extractor` | active |
| Bot 服务在跑 | `systemctl status stockrec-bot` | active |
| 1小时入库 ≥ 50 | SQL 查询 | yes |
| LLM 提取有产出 | SQL 查询 | yes |
| AKShare 行情入库 | 看 daily_quotes 表 | T 日有数据 |
| Telegram 收到测试消息 | 手动 `python pusher.py` | 收到 |
| 健康检查脚本 | 手动跑一遍 | 无误报 |

## 常见坑总结

1. **雪球 Cookie 过期**：1-3 个月失效一次，给 healthcheck 加一条"特定 RSS 是否有内容"检查
2. **AKShare 字段名变动**：每次升级 AKShare 后跑一次单元测试
3. **DeepSeek 限流**：高峰期会偶尔 429，tenacity 已加重试，但要监控成本
4. **Telegram Markdown V2 转义**：很多特殊字符必须转义，否则整条消息发不出
5. **A股春节/国庆休市**：cron 会在节假日空跑，可以加个 trading_calendar 判断
6. **look-ahead bias**：T 日的"最新研报"如果是盘后发布，不能用于 T 日的回测验证
7. **概念漂移**：题材轮动很快，6 个月前的"AI算力"和现在的不是一个东西，IC 计算窗口不要拉太长

## Cron 汇总

```bash
# 每个交易日 15:30、16:30、17:30 跑行情
30 15,16,17 * * 1-5 /home/$USER/stock-recommender/venv/bin/python /home/$USER/stock-recommender/collector/src/market_data.py >> /home/$USER/stock-recommender/logs/market.log 2>&1

# 每个交易日 17:00 跑聚合（确保行情/龙虎榜已采集）
0 17 * * 1-5 /home/$USER/stock-recommender/venv/bin/python /home/$USER/stock-recommender/strategy/src/aggregate.py >> /home/$USER/stock-recommender/logs/strategy.log 2>&1

# 收盘后推送（注意是工作日）
30 17 * * 1-5 /home/$USER/stock-recommender/venv/bin/python /home/$USER/stock-recommender/bot/src/pusher.py >> /home/$USER/stock-recommender/logs/push.log 2>&1

# T+1/T+5/T+20 业绩跟踪
0 18 * * 1-5 /home/$USER/stock-recommender/venv/bin/python /home/$USER/stock-recommender/strategy/src/track_performance.py >> /home/$USER/stock-recommender/logs/track.log 2>&1

# 每周日 20:00 计算源 IC 并调整权重
0 20 * * 0 /home/$USER/stock-recommender/venv/bin/python /home/$USER/stock-recommender/strategy/src/source_ic.py >> /home/$USER/stock-recommender/logs/ic.log 2>&1

# 每 15 分钟健康检查
*/15 * * * * /home/$USER/stock-recommender/configs/healthcheck.sh

# 每天凌晨 3 点数据库备份
0 3 * * * /home/$USER/stock-recommender/configs/backup.sh
```
