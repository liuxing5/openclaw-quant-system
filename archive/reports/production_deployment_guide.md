# 生产环境部署指南

## 📋 文档概述

本文档提供OpenClaw量化交易系统生产环境部署的完整指南，涵盖系统架构、环境配置、监控部署、安全设置和运维流程。

## 🏗️ 系统架构

### 核心组件
```
├── quant_system/                    # 量化系统核心
│   ├── data/                       # 数据层
│   ├── enhancements/               # 策略增强模块
│   ├── walkforward/               # Walk-forward回测框架
│   ├── utils/                     # 工具模块
│   │   ├── liquidity_calculator.py   # 流动性计算器
│   │   ├── market_cap_fetcher.py     # 市值数据获取器
│   │   └── orderbook_stats.py        # OrderBook统计模块
│   └── web_dashboard/              # 监控仪表板
└── config/                         # 配置文件
```

### 数据流向
```
数据源 (Baostock/AKShare) → 数据管道 → 数据库 → 量化引擎 → 回测结果 → 监控仪表板
```

## 🔧 环境要求

### 硬件要求
| 组件 | 最低配置 | 推荐配置 | 说明 |
|------|----------|----------|------|
| **CPU** | 4核心 | 8核心+ | 回测需要并行计算 |
| **内存** | 8GB | 16GB+ | 数据缓存和并行处理 |
| **存储** | 50GB SSD | 200GB NVMe | 历史数据和缓存 |
| **网络** | 10Mbps | 100Mbps+ | 实时数据获取 |

### 软件要求
| 软件 | 版本 | 安装方式 | 说明 |
|------|------|----------|------|
| **Python** | 3.9+ | 系统包管理 | 核心运行时 |
| **Node.js** | 18+ | 可选 | Web仪表板前端增强 |
| **Redis** | 6.0+ | Docker/包管理 | 缓存和任务队列 |
| **PostgreSQL** | 13+ | Docker/包管理 | 生产数据库（可选） |
| **Nginx** | 1.18+ | 包管理 | 反向代理和负载均衡 |

### Python包依赖
```bash
# 核心依赖
pip install pandas>=2.0 numpy>=1.24 scipy>=1.10 scikit-learn>=1.3
pip install baostock akshare tushare yfinance

# Web框架
pip install flask>=3.0 flask-cors gunicorn

# 量化专用
pip install empyrical pyfolio riskfolio-ml

# 监控和日志
pip install prometheus-client psutil
```

## 🚀 部署步骤

### 步骤1：系统初始化

```bash
# 1.1 创建专用用户
sudo useradd -m -s /bin/bash quant
sudo passwd quant

# 1.2 创建工作目录
sudo mkdir -p /opt/quant-system
sudo chown -R quant:quant /opt/quant-system

# 1.3 切换到quant用户
su - quant
cd /opt/quant-system
```

### 步骤2：代码部署

```bash
# 2.1 克隆代码库
git clone https://github.com/liuxing5/openclaw-quant-system.git .
git checkout master

# 2.2 创建Python虚拟环境
python3 -m venv venv
source venv/bin/activate

# 2.3 安装依赖
pip install --upgrade pip
pip install -r requirements.txt  # 如果存在
# 或手动安装核心依赖（见上一节）
```

### 步骤3：配置文件

#### 3.1 主配置文件 `config/production.yaml`
```yaml
# 系统配置
system:
  name: "quant-production"
  environment: "production"
  timezone: "Asia/Shanghai"
  log_level: "INFO"

# 数据源配置
data_sources:
  baostock:
    enabled: true
    retry_times: 3
    timeout_seconds: 30
  akshare:
    enabled: true
    fallback_enabled: true

# 数据库配置
database:
  type: "sqlite"  # 或 "postgresql"
  sqlite_path: "/opt/quant-system/data/quant.db"
  postgresql:
    host: "localhost"
    port: 5432
    database: "quant"
    username: "quant_user"
    password: "secure_password"

# 回测配置
backtest:
  default_initial_capital: 1000000
  default_slippage_rate: 0.002
  use_advanced_slippage: true
  adv_threshold: 3000.0
  market_cap_threshold: 30.0

# 监控配置
monitoring:
  stats_dir: "/opt/quant-system/stats"
  retention_days: 30
  enable_web_dashboard: true
  dashboard_port: 5000
  prometheus_port: 9090

# 安全配置
security:
  enable_auth: true
  api_key_required: true
  rate_limit_per_minute: 60
```

#### 3.2 环境变量 `.env`
```bash
# 数据库
export DATABASE_URL="sqlite:///opt/quant-system/data/quant.db"
export REDIS_URL="redis://localhost:6379/0"

# API密钥
export BAOSTOCK_USERNAME=""
export BAOSTOCK_PASSWORD=""

# 安全
export SECRET_KEY="your-secret-key-here"
export API_KEY="your-api-key-here"

# 监控
export PROMETHEUS_MULTIPROC_DIR="/opt/quant-system/tmp"
```

### 步骤4：数据库初始化

```bash
# 4.1 创建数据目录
mkdir -p /opt/quant-system/{data,stats,logs,tmp}

# 4.2 初始化SQLite数据库
cd /opt/quant-system
python -c "
from quant_system.data.database.database_manager import DatabaseManager
db = DatabaseManager('data/quant.db')
db.initialize_tables()
print('✅ 数据库初始化完成')
"

# 4.3 创建必要的缓存目录
mkdir -p /opt/quant-system/cache/{baostock,akshare}
```

### 步骤5：监控仪表板部署

#### 5.1 启动Web仪表板
```bash
# 开发模式（测试用）
cd /opt/quant-system/quant_system/web_dashboard
python app.py

# 生产模式（使用Gunicorn）
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 --access-logfile logs/dashboard-access.log app:app
```

#### 5.2 配置Nginx反向代理
```nginx
# /etc/nginx/sites-available/quant-dashboard
server {
    listen 80;
    server_name quant.yourdomain.com;
    
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /static/ {
        alias /opt/quant-system/quant_system/web_dashboard/static/;
        expires 1d;
    }
    
    # 安全头
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
}
```

#### 5.3 启用站点并重启Nginx
```bash
sudo ln -s /etc/nginx/sites-available/quant-dashboard /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 步骤6：系统服务配置

#### 6.1 创建系统服务文件
创建 `/etc/systemd/system/quant-dashboard.service`：
```ini
[Unit]
Description=Quant System Dashboard
After=network.target
Wants=network.target

[Service]
Type=simple
User=quant
Group=quant
WorkingDirectory=/opt/quant-system/quant_system/web_dashboard
Environment="PATH=/opt/quant-system/venv/bin"
EnvironmentFile=/opt/quant-system/.env
ExecStart=/opt/quant-system/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 --access-logfile /opt/quant-system/logs/dashboard-access.log app:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 6.2 创建数据更新服务
创建 `/etc/systemd/system/quant-data-update.service`：
```ini
[Unit]
Description=Quant System Data Update Service
After=network.target

[Service]
Type=oneshot
User=quant
Group=quant
WorkingDirectory=/opt/quant-system
Environment="PATH=/opt/quant-system/venv/bin"
EnvironmentFile=/opt/quant-system/.env
ExecStart=/opt/quant-system/venv/bin/python -m quant_system.data.database.backfill_all_stocks
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

#### 6.3 创建定时更新
创建 `/etc/systemd/system/quant-data-update.timer`：
```ini
[Unit]
Description=Daily stock data update

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

#### 6.4 启用服务
```bash
sudo systemctl daemon-reload
sudo systemctl enable quant-dashboard.service
sudo systemctl enable quant-data-update.timer
sudo systemctl start quant-dashboard.service
sudo systemctl start quant-data-update.timer
```

### 步骤7：安全配置

#### 7.1 防火墙配置
```bash
# 只开放必要端口
sudo ufw allow 22/tcp      # SSH
sudo ufw allow 80/tcp      # HTTP
sudo ufw allow 443/tcp     # HTTPS（如果使用SSL）
sudo ufw enable
```

#### 7.2 SSL证书配置（使用Let's Encrypt）
```bash
# 安装Certbot
sudo apt install certbot python3-certbot-nginx

# 获取证书
sudo certbot --nginx -d quant.yourdomain.com

# 自动续期测试
sudo certbot renew --dry-run
```

#### 7.3 文件权限设置
```bash
sudo chown -R quant:quant /opt/quant-system
sudo chmod 750 /opt/quant-system
sudo chmod 640 /opt/quant-system/.env
sudo find /opt/quant-system -type f -name "*.py" -exec chmod 644 {} \;
```

### 步骤8：监控和告警

#### 8.1 安装和配置Prometheus
```bash
# 安装Prometheus
wget https://github.com/prometheus/prometheus/releases/download/v2.45.0/prometheus-2.45.0.linux-amd64.tar.gz
tar xvf prometheus-2.45.0.linux-amd64.tar.gz
sudo mv prometheus-2.45.0.linux-amd64 /opt/prometheus
```

#### 8.2 配置Prometheus监控目标
编辑 `/opt/prometheus/prometheus.yml`：
```yaml
scrape_configs:
  - job_name: 'quant-system'
    static_configs:
      - targets: ['localhost:9091']
    metrics_path: '/metrics'
```

#### 8.3 在仪表板中暴露指标
在 `web_dashboard/app.py` 中添加：
```python
from prometheus_client import generate_latest, Counter, Histogram, REGISTRY

# 定义指标
ORDER_CALLS = Counter('orderbook_total_calls', 'Total order calls')
ORDER_EXECUTION_TIME = Histogram('orderbook_execution_time', 'Order execution time')

# 添加/metrics端点
@app.route('/metrics')
def metrics():
    return generate_latest(REGISTRY)
```

#### 8.4 配置告警规则
创建 `/opt/prometheus/alerts.yml`：
```yaml
groups:
  - name: quant_alerts
    rules:
      - alert: HighErrorRate
        expr: rate(orderbook_errors_total[5m]) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "高错误率检测"
          
      - alert: DashboardDown
        expr: up{job="quant-system"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "监控仪表板宕机"
```

## 📊 性能优化

### 数据库优化
```python
# SQLite生产配置
import sqlite3

def get_production_connection(db_path):
    conn = sqlite3.connect(db_path)
    
    # WAL模式（提高并发性能）
    conn.execute('PRAGMA journal_mode=WAL')
    
    # 提高缓存大小
    conn.execute('PRAGMA cache_size=-20000')  # 20MB
    
    # 同步设置（安全与性能平衡）
    conn.execute('PRAGMA synchronous=NORMAL')
    
    # 内存映射
    conn.execute('PRAGMA mmap_size=268435456')  # 256MB
    
    return conn
```

### 内存缓存优化
```python
# 使用LRU缓存
from functools import lru_cache
import hashlib

@lru_cache(maxsize=1000)
def get_stock_data_cached(symbol, start_date, end_date):
    # 缓存股票数据
    pass

# 缓存键生成
def generate_cache_key(*args, **kwargs):
    key_str = str(args) + str(sorted(kwargs.items()))
    return hashlib.md5(key_str.encode()).hexdigest()
```

### 并发处理
```python
# 使用线程池处理并发回测
from concurrent.futures import ThreadPoolExecutor, as_completed

def run_batch_backtest_concurrent(symbols, config, max_workers=4):
    results = {}
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {
            executor.submit(run_single_backtest, symbol, config): symbol
            for symbol in symbols
        }
        
        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            try:
                results[symbol] = future.result()
            except Exception as e:
                results[symbol] = {'error': str(e)}
    
    return results
```

## 🔍 故障排除

### 常见问题

#### 问题1：数据库连接失败
**症状**：`sqlite3.OperationalError: unable to open database file`
**解决方案**：
```bash
# 检查文件权限
ls -la /opt/quant-system/data/

# 修复权限
sudo chown quant:quant /opt/quant-system/data/quant.db
sudo chmod 664 /opt/quant-system/data/quant.db
```

#### 问题2：内存不足
**症状**：回测时`MemoryError`或进程被杀死
**解决方案**：
```python
# 优化内存使用
import pandas as pd

# 使用数据类型优化
dtype_optimized = {
    'open': 'float32',
    'high': 'float32', 
    'low': 'float32',
    'close': 'float32',
    'volume': 'int32'
}

# 分块处理大数据
def process_large_data_in_chunks(file_path, chunk_size=10000):
    for chunk in pd.read_csv(file_path, chunksize=chunk_size):
        process_chunk(chunk)
```

#### 问题3：数据源API限制
**症状**：`HTTP 429 Too Many Requests`或连接超时
**解决方案**：
```python
# 添加重试和退避机制
import time
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_data_with_retry(symbol):
    # 数据获取逻辑
    pass

# 添加请求间隔
def throttled_request(url):
    time.sleep(0.1)  # 100ms间隔
    return requests.get(url)
```

#### 问题4：监控仪表板无法访问
**症状**：`Connection refused`或`502 Bad Gateway`
**解决方案**：
```bash
# 检查服务状态
sudo systemctl status quant-dashboard.service

# 检查日志
sudo journalctl -u quant-dashboard.service -f

# 检查端口占用
sudo netstat -tlnp | grep :5000

# 重启服务
sudo systemctl restart quant-dashboard.service
```

### 日志分析

#### 日志位置
- 仪表板访问日志：`/opt/quant-system/logs/dashboard-access.log`
- 系统日志：`/var/log/syslog`
- 应用日志：`/opt/quant-system/logs/application.log`

#### 关键日志模式
```bash
# 查看错误日志
grep -i "error\|exception\|traceback" /opt/quant-system/logs/application.log

# 查看慢查询
grep "execution_time.*[5-9][0-9][0-9][0-9]" /opt/quant-system/logs/application.log

# 监控API调用频率
tail -f /opt/quant-system/logs/dashboard-access.log | awk '{print $4}' | uniq -c | sort -nr
```

## 🔄 备份和恢复

### 备份策略

#### 每日自动备份
创建备份脚本 `/opt/quant-system/scripts/backup.sh`：
```bash
#!/bin/bash
BACKUP_DIR="/opt/quant-system/backups"
DATE=$(date +%Y%m%d_%H%M%S)

# 创建备份目录
mkdir -p $BACKUP_DIR

# 备份数据库
cp /opt/quant-system/data/quant.db $BACKUP_DIR/quant_$DATE.db

# 备份配置文件
tar czf $BACKUP_DIR/config_$DATE.tar.gz /opt/quant-system/config/

# 备份统计数据和日志
tar czf $BACKUP_DIR/stats_$DATE.tar.gz /opt/quant-system/stats/

# 清理旧备份（保留最近30天）
find $BACKUP_DIR -name "*.db" -mtime +30 -delete
find $BACKUP_DIR -name "*.tar.gz" -mtime +30 -delete

echo "备份完成: $DATE"
```

#### 设置定时备份
```bash
# 添加到crontab
crontab -e

# 每天凌晨2点执行备份
0 2 * * * /opt/quant-system/scripts/backup.sh >> /opt/quant-system/logs/backup.log 2>&1
```

### 恢复流程

#### 数据库恢复
```bash
# 停止服务
sudo systemctl stop quant-dashboard.service

# 恢复数据库
cp /opt/quant-system/backups/quant_20240322.db /opt/quant-system/data/quant.db

# 修复权限
chown quant:quant /opt/quant-system/data/quant.db
chmod 664 /opt/quant-system/data/quant.db

# 启动服务
sudo systemctl start quant-dashboard.service
```

#### 完整系统恢复
```bash
# 1. 恢复代码
cd /opt/quant-system
git fetch origin
git reset --hard origin/master

# 2. 恢复虚拟环境
source venv/bin/activate
pip install -r requirements.txt

# 3. 恢复数据
tar xzf /opt/quant-system/backups/stats_20240322.tar.gz -C /

# 4. 重启所有服务
sudo systemctl restart quant-dashboard.service
sudo systemctl restart quant-data-update.timer
```

## 📈 扩展和升级

### 水平扩展

#### 添加工作节点
```bash
# 在工作节点上重复部署步骤
# 修改配置使用共享数据库

# 配置负载均衡器
upstream quant_servers {
    server 192.168.1.10:5000;  # 主节点
    server 192.168.1.11:5000;  # 工作节点1
    server 192.168.1.12:5000;  # 工作节点2
    least_conn;  # 最少连接负载均衡
}
```

#### 数据库分片
```python
# 按股票代码分片
def get_shard_connection(symbol):
    shard_id = hash(symbol) % 4  # 4个分片
    db_path = f"/opt/quant-system/data/shard_{shard_id}.db"
    return sqlite3.connect(db_path)
```

### 垂直升级

#### 升级硬件
```bash
# 监控系统性能指标
cd /opt/quant-system
python -c "
import psutil
print(f'CPU使用率: {psutil.cpu_percent()}%')
print(f'内存使用: {psutil.virtual_memory().percent}%')
print(f'磁盘使用: {psutil.disk_usage("/").percent}%')
"
```

#### 优化配置
```yaml
# config/advanced.yaml
performance:
  max_workers: 8  # 增加到8个工作进程
  cache_size_mb: 1024  # 增加到1GB缓存
  batch_size: 1000  # 增加批处理大小
```

## 📞 支持和维护

### 监控指标
| 指标 | 正常范围 | 告警阈值 | 检查命令 |
|------|----------|----------|----------|
| **CPU使用率** | <70% | >90%持续5分钟 | `top -bn1 | grep "Cpu(s)"` |
| **内存使用率** | <80% | >95% | `free -m` |
| **磁盘使用率** | <85% | >95% | `df -h` |
| **服务响应时间** | <500ms | >2000ms | `curl -o /dev/null -s -w '%{time_total}'` |
| **API成功率** | >99% | <95% | 监控日志 |

### 联系信息
- **技术负责人**: [姓名]
- **紧急联系人**: [电话]
- **监控告警**: [邮件/钉钉/微信]
- **文档位置**: `/opt/quant-system/docs/`

### 定期维护任务
```bash
# 每周一凌晨3点执行维护
0 3 * * 1 /opt/quant-system/scripts/maintenance.sh

# 维护脚本内容
#!/bin/bash
echo "开始系统维护 $(date)"

# 1. 清理临时文件
find /opt/quant-system/tmp -type f -mtime +7 -delete

# 2. 优化数据库
/opt/quant-system/venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('/opt/quant-system/data/quant.db')
conn.execute('VACUUM')
conn.execute('ANALYZE')
conn.close()
print('数据库优化完成')
"

# 3. 更新数据
/opt/quant-system/venv/bin/python -m quant_system.data.database.backfill_all_stocks

echo "系统维护完成 $(date)"
```

---

**文档版本**: v1.0  
**最后更新**: 2026-03-22  
**适用环境**: 生产环境  
**文档状态**: ✅ 已完成