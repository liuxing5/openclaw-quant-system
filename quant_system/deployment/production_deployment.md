# 生产级部署与监控方案

## 一、容器化部署 (Docker + Kubernetes)

### 1.1 Docker镜像构建

#### Dockerfile
```dockerfile
# 基础镜像
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    build-essential \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建非root用户
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# 启动命令
CMD ["python", "main.py"]
```

#### requirements.txt
```txt
# 基础依赖
pandas==2.2.0
numpy==1.26.4
scipy==1.12.0

# 数据源
baostock==0.8.9
akshare==1.12.80
tushare==1.2.89

# 量化分析
empyrical==0.5.5
pyfolio==0.9.2
riskfolio-lib==4.2.0
alphalens==0.4.3

# 机器学习
scikit-learn==1.4.0
statsmodels==0.14.1

# Web框架
fastapi==0.108.0
uvicorn[standard]==0.25.0
websockets==12.0

# 数据库
sqlalchemy==2.0.23
aiosqlite==0.19.0
redis==5.0.1

# 监控
prometheus-client==0.19.0
opentelemetry-api==1.22.0
opentelemetry-sdk==1.22.0

# 其他
python-dotenv==1.0.0
loguru==0.7.2
croniter==2.0.3
```

### 1.2 Docker Compose编排

#### docker-compose.yml
```yaml
version: '3.8'

services:
  # 量化策略服务
  quant-strategy:
    build: .
    image: quant-strategy:latest
    container_name: quant-strategy
    restart: unless-stopped
    environment:
      - ENVIRONMENT=production
      - DATABASE_URL=postgresql://user:pass@postgres:5432/quantdb
      - REDIS_URL=redis://redis:6379/0
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - redis
    networks:
      - quant-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  # 数据采集服务
  data-collector:
    build: .
    image: quant-strategy:latest
    container_name: data-collector
    restart: unless-stopped
    command: python data_collector.py
    environment:
      - ENVIRONMENT=production
      - DATABASE_URL=postgresql://user:pass@postgres:5432/quantdb
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    depends_on:
      - postgres
    networks:
      - quant-network

  # 回测引擎服务
  backtest-engine:
    build: .
    image: quant-strategy:latest
    container_name: backtest-engine
    restart: unless-stopped
    command: python backtest_engine.py
    environment:
      - ENVIRONMENT=production
      - DATABASE_URL=postgresql://user:pass@postgres:5432/quantdb
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    depends_on:
      - postgres
    networks:
      - quant-network

  # PostgreSQL数据库
  postgres:
    image: postgres:15-alpine
    container_name: postgres
    restart: unless-stopped
    environment:
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
      - POSTGRES_DB=quantdb
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
    ports:
      - "5432:5432"
    networks:
      - quant-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U user"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Redis缓存
  redis:
    image: redis:7-alpine
    container_name: redis
    restart: unless-stopped
    volumes:
      - redis-data:/data
    ports:
      - "6379:6379"
    networks:
      - quant-network
    command: redis-server --appendonly yes
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Prometheus监控
  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    restart: unless-stopped
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus
    ports:
      - "9090:9090"
    networks:
      - quant-network

  # Grafana可视化
  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    restart: unless-stopped
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana-data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
    ports:
      - "3000:3000"
    depends_on:
      - prometheus
    networks:
      - quant-network

  # 告警管理器
  alertmanager:
    image: prom/alertmanager:latest
    container_name: alertmanager
    restart: unless-stopped
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml
    ports:
      - "9093:9093"
    networks:
      - quant-network

networks:
  quant-network:
    driver: bridge

volumes:
  postgres-data:
  redis-data:
  prometheus-data:
  grafana-data:
```

### 1.3 Kubernetes部署配置

#### deployment.yaml
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: quant-strategy
  namespace: quant-system
  labels:
    app: quant-strategy
    tier: backend
spec:
  replicas: 3
  selector:
    matchLabels:
      app: quant-strategy
  template:
    metadata:
      labels:
        app: quant-strategy
    spec:
      containers:
      - name: quant-strategy
        image: quant-strategy:latest
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: 8000
        env:
        - name: ENVIRONMENT
          value: "production"
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: quant-secrets
              key: database-url
        - name: REDIS_URL
          value: "redis://redis-service:6379/0"
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
        volumeMounts:
        - name: data-volume
          mountPath: /app/data
        - name: logs-volume
          mountPath: /app/logs
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 30
          timeoutSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
          timeoutSeconds: 5
      volumes:
      - name: data-volume
        persistentVolumeClaim:
          claimName: quant-data-pvc
      - name: logs-volume
        emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: quant-strategy-service
  namespace: quant-system
spec:
  selector:
    app: quant-strategy
  ports:
  - port: 8000
    targetPort: 8000
  type: ClusterIP
```

#### service.yaml
```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres-service
  namespace: quant-system
spec:
  selector:
    app: postgres
  ports:
  - port: 5432
    targetPort: 5432
---
apiVersion: v1
kind: Service
metadata:
  name: redis-service
  namespace: quant-system
spec:
  selector:
    app: redis
  ports:
  - port: 6379
    targetPort: 6379
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus-service
  namespace: monitoring
spec:
  selector:
    app: prometheus
  ports:
  - port: 9090
    targetPort: 9090
  type: NodePort
---
apiVersion: v1
kind: Service
metadata:
  name: grafana-service
  namespace: monitoring
spec:
  selector:
    app: grafana
  ports:
  - port: 3000
    targetPort: 3000
  type: NodePort
```

---

## 二、Prometheus + Grafana监控体系

### 2.1 Prometheus配置

#### prometheus.yml
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    environment: 'production'
    cluster: 'quant-cluster'

rule_files:
  - 'alert_rules.yml'

scrape_configs:
  # 量化策略应用
  - job_name: 'quant-strategy'
    static_configs:
      - targets: ['quant-strategy-service.quant-system.svc.cluster.local:8000']
    metrics_path: '/metrics'
    scrape_interval: 30s
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance
      - source_labels: [__meta_kubernetes_pod_name]
        target_label: pod

  # 数据采集服务
  - job_name: 'data-collector'
    static_configs:
      - targets: ['data-collector-service.quant-system.svc.cluster.local:8001']
    scrape_interval: 60s

  # 回测引擎
  - job_name: 'backtest-engine'
    static_configs:
      - targets: ['backtest-engine-service.quant-system.svc.cluster.local:8002']
    scrape_interval: 60s

  # 数据库
  - job_name: 'postgres-exporter'
    static_configs:
      - targets: ['postgres-exporter.monitoring.svc.cluster.local:9187']

  # Redis
  - job_name: 'redis-exporter'
    static_configs:
      - targets: ['redis-exporter.monitoring.svc.cluster.local:9121']

  # Kubernetes节点
  - job_name: 'kubernetes-nodes'
    kubernetes_sd_configs:
      - role: node
    relabel_configs:
      - source_labels: [__address__]
        regex: '(.*):10250'
        replacement: '${1}:9100'
        target_label: __address__
      - action: labelmap
        regex: __meta_kubernetes_node_label_(.+)

  # Kubernetes Pods
  - job_name: 'kubernetes-pods'
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: true
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
        action: replace
        target_label: __metrics_path__
        regex: (.+)
      - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
        action: replace
        regex: ([^:]+)(?::\d+)?;(\d+)
        replacement: $1:$2
        target_label: __address__
      - action: labelmap
        regex: __meta_kubernetes_pod_label_(.+)
      - source_labels: [__meta_kubernetes_namespace]
        action: replace
        target_label: kubernetes_namespace
      - source_labels: [__meta_kubernetes_pod_name]
        action: replace
        target_label: kubernetes_pod_name
```

#### alert_rules.yml
```yaml
groups:
  - name: quant_strategy_alerts
    rules:
      # 应用健康检查
      - alert: QuantStrategyDown
        expr: up{job="quant-strategy"} == 0
        for: 1m
        labels:
          severity: critical
          service: quant-strategy
        annotations:
          summary: "量化策略服务宕机"
          description: "量化策略服务 {{ $labels.instance }} 已宕机超过1分钟"

      # 高错误率
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5..", job="quant-strategy"}[5m]) > 0.05
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "高错误率告警"
          description: "5分钟内错误率超过5%"

      # 高延迟
      - alert: HighLatency
        expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{job="quant-strategy"}[5m])) > 1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "高延迟告警"
          description: "95%分位请求延迟超过1秒"

      # 内存使用率
      - alert: HighMemoryUsage
        expr: (container_memory_working_set_bytes{container="quant-strategy"} / container_spec_memory_limit_bytes{container="quant-strategy"}) > 0.8
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "内存使用率过高"
          description: "量化策略服务内存使用率超过80%"

      # CPU使用率
      - alert: HighCPUUsage
        expr: rate(container_cpu_usage_seconds_total{container="quant-strategy"}[5m]) > 0.8
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "CPU使用率过高"
          description: "量化策略服务CPU使用率超过80%"

      # 数据库连接数
      - alert: HighDatabaseConnections
        expr: postgresql_connections > 50
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "数据库连接数过高"
          description: "PostgreSQL连接数超过50"

      # Redis内存使用
      - alert: HighRedisMemory
        expr: redis_memory_used_bytes / redis_memory_max_bytes > 0.8
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Redis内存使用率过高"
          description: "Redis内存使用率超过80%"

  - name: trading_alerts
    rules:
      # 交易异常
      - alert: TradingVolumeAnomaly
        expr: abs(rate(trading_volume_total[1h]) - rate(trading_volume_total[1h] offset 1d)) / rate(trading_volume_total[1h] offset 1d) > 2
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "交易量异常"
          description: "当前交易量相比昨日同期异常波动"

      # 策略回撤过大
      - alert: HighStrategyDrawdown
        expr: strategy_drawdown_percent > 0.1
        for: 30m
        labels:
          severity: critical
        annotations:
          summary: "策略回撤过大"
          description: "策略回撤超过10%"

      # 风险指标异常
      - alert: RiskMetricAnomaly
        expr: var_95_percentile > var_limit * 1.2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "风险指标异常"
          description: "VaR超过限额20%"
```

### 2.2 Grafana仪表板配置

#### 仪表板配置 (JSON)
```json
{
  "dashboard": {
    "title": "量化策略监控仪表板",
    "tags": ["quant", "trading", "monitoring"],
    "timezone": "browser",
    "panels": [
      {
        "title": "服务健康状态",
        "type": "stat",
        "targets": [
          {
            "expr": "up{job=\"quant-strategy\"}",
            "legendFormat": "{{instance}}"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "color": {
              "mode": "thresholds"
            },
            "thresholds": {
              "steps": [
                {"color": "red", "value": null},
                {"color": "green", "value": 1}
              ]
            }
          }
        }
      },
      {
        "title": "请求延迟 (95%分位)",
        "type": "graph",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{job=\"quant-strategy\"}[5m]))",
            "legendFormat": "{{method}} {{endpoint}}"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "s"
          }
        }
      },
      {
        "title": "错误率",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(http_requests_total{status=~\"5..\", job=\"quant-strategy\"}[5m]) / rate(http_requests_total{job=\"quant-strategy\"}[5m])",
            "legendFormat": "错误率"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "percentunit"
          }
        }
      },
      {
        "title": "内存使用率",
        "type": "graph",
        "targets": [
          {
            "expr": "container_memory_working_set_bytes{container=\"quant-strategy\"} / container_spec_memory_limit_bytes{container=\"quant-strategy\"}",
            "legendFormat": "内存使用率"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "percentunit"
          }
        }
      },
      {
        "title": "CPU使用率",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(container_cpu_usage_seconds_total{container=\"quant-strategy\"}[5m])",
            "legendFormat": "CPU使用率"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "percentunit"
          }
        }
      },
      {
        "title": "策略绩效指标",
        "type": "stat",
        "gridPos": {"h": 4, "w": 6, "x": 0, "y": 12},
        "targets": [
          {
            "expr": "strategy_total_return",
            "legendFormat": "总收益"
          },
          {
            "expr": "strategy_sharpe_ratio",
            "legendFormat": "夏普比率"
          },
          {
            "expr": "strategy_max_drawdown",
            "legendFormat": "最大回撤"
          }
        ]
      },
      {
        "title": "风险指标监控",
        "type": "graph",
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 16},
        "targets": [
          {
            "expr": "var_95_percentile",
            "legendFormat": "VaR(95%)"
          },
          {
            "expr": "cvar_95_percentile",
            "legendFormat": "CVaR(95%)"
          },
          {
            "expr": "var_limit",
            "legendFormat": "VaR限额"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "percent"
          }
        }
      }
    ]
  }
}
```

---

## 三、全链路日志系统

### 3.1 结构化日志配置

#### logging_config.py
```python
import logging
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

class StructuredLogger:
    """结构化日志记录器"""
    
    def __init__(self, name: str, log_dir: str = "logs"):
        self.name = name
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        
        # 设置日志格式
        self.formatter = self._create_formatter()
        
        # 创建日志处理器
        self.handlers = self._create_handlers()
        
        # 配置日志记录器
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        
        for handler in self.handlers:
            self.logger.addHandler(handler)
    
    def _create_formatter(self):
        """创建日志格式化器"""
        class JsonFormatter(logging.Formatter):
            def format(self, record):
                log_record = {
                    'timestamp': datetime.utcnow().isoformat() + 'Z',
                    'level': record.levelname,
                    'logger': record.name,
                    'message': record.getMessage(),
                    'module': record.module,
                    'function': record.funcName,
                    'line': record.lineno,
                    'thread': record.threadName,
                    'process': record.processName,
                }
                
                # 添加额外字段
                if hasattr(record, 'extra'):
                    log_record.update(record.extra)
                
                # 添加异常信息
                if record.exc_info:
                    log_record['exception'] = self.formatException(record.exc_info)
                
                return json.dumps(log_record, ensure_ascii=False)
        
        return JsonFormatter()
    
    def _create_handlers(self):
        """创建日志处理器"""
        handlers = []
        
        # 文件处理器（JSON格式）
        file_handler = logging.FileHandler(
            self.log_dir / f"{self.name}.jsonl",
            encoding='utf-8'
        )
        file_handler.setFormatter(self.formatter)
        handlers.append(file_handler)
        
        # 控制台处理器（人类可读格式）
        console_handler = logging.StreamHandler(sys.stdout)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        handlers.append(console_handler)
        
        return handlers
    
    def info(self, message: str, **extra):
        """记录INFO级别日志"""
        self._log(logging.INFO, message, extra)
    
    def warning(self, message: str, **extra):
        """记录WARNING级别日志"""
        self._log(logging.WARNING, message, extra)
    
    def error(self, message: str, **extra):
        """记录ERROR级别日志"""
        self._log(logging.ERROR, message, extra)
    
    def critical(self, message: str, **extra):
        """记录CRITICAL级别日志"""
        self._log(logging.CRITICAL, message, extra)
    
    def _log(self, level: int, message: str, extra: Dict[str, Any]):
        """通用日志记录方法"""
        record = self.logger.makeRecord(
            self.name, level, '', 0, message, None, None,
            extra=extra
        )
        self.logger.handle(record)


# 应用日志记录器
class ApplicationLogger:
    """应用日志记录器"""
    
    def __init__(self):
        self.logger = StructuredLogger('quant-strategy')
        
        # 业务特定日志记录器
        self.trading_logger = StructuredLogger('trading')
        self.risk_logger = StructuredLogger('risk')
        self.data_logger = StructuredLogger('data')
        self.backtest_logger = StructuredLogger('backtest')
    
    def log_request(self, request_id: str, endpoint: str, method: str, duration: float, status: int):
        """记录请求日志"""
        self.logger.info(
            "HTTP请求完成",
            request_id=request_id,
            endpoint=endpoint,
            method=method,
            duration_ms=duration * 1000,
            status=status,
            log_type="request"
        )
    
    def log_trading_signal(self, symbol: str, signal: str, confidence: float, factors: Dict):
        """记录交易信号"""
        self.trading_logger.info(
            "生成交易信号",
            symbol=symbol,
            signal=signal,
            confidence=confidence,
            factors=factors,
            log_type="trading_signal"
        )
    
    def log_risk_alert(self, alert_type: str, level: str, message: str, metrics: Dict):
        """记录风险告警"""
        self.risk_logger.warning(
            f"风险告警: {alert_type}",
            alert_type=alert_type,
            level=level,
            message=message,
            metrics=metrics,
            log_type="risk_alert"
        )
    
    def log_data_error(self, source: str, error: str, symbol: str = None):
        """记录数据错误"""
        self.data_logger.error(
            f"数据采集错误: {source}",
            source=source,
            error=error,
            symbol=symbol,
            log_type="data_error"
        )
    
    def log_backtest_result(self, strategy: str, result: Dict):
        """记录回测结果"""
        self.backtest_logger.info(
            f"回测完成: {strategy}",
            strategy=strategy,
            result=result,
            log_type="backtest_result"
        )


# 全局日志实例
logger = ApplicationLogger()
```

### 3.2 分布式追踪配置

#### tracing_config.py
```python
import opentelemetry
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor

def setup_tracing(service_name: str = "quant-strategy"):
    """设置分布式追踪"""
    
    # 创建资源
    resource = Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
        "environment": "production"
    })
    
    # 创建追踪提供者
    tracer_provider = TracerProvider(resource=resource)
    
    # 配置导出器
    # Jaeger导出器（开发环境）
    jaeger_exporter = JaegerExporter(
        agent_host_name="localhost",
        agent_port=6831,
    )
    
    # OTLP导出器（生产环境）
    otlp_exporter = OTLPSpanExporter(
        endpoint="http://jaeger-collector:4317",
        insecure=True
    )
    
    # 添加处理器
    tracer_provider.add_span_processor(
        BatchSpanProcessor(jaeger_exporter)
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(otlp_exporter)
    )
    
    # 设置全局追踪提供者
    trace.set_tracer_provider(tracer_provider)
    
    # 获取全局追踪器
    tracer = trace.get_tracer(__name__)
    
    # 自动检测
    FastAPIInstrumentor().instrument()
    RequestsInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument()
    RedisInstrumentor().instrument()
    
    return tracer


# 追踪装饰器
def trace_span(name: str, attributes: dict = None):
    """追踪装饰器"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span(name, attributes=attributes) as span:
                try:
                    result = func(*args, **kwargs)
                    span.set_status(opentelemetry.trace.StatusCode.OK)
                    return result
                except Exception as e:
                    span.set_status(opentelemetry.trace.StatusCode.ERROR)
                    span.record_exception(e)
                    raise
        return wrapper
    return decorator
```

---

## 四、异常处理与告警

### 4.1 异常处理框架

#### exception_handler.py
```python
import sys
import traceback
from typing import Optional, Type, Dict, Any
from functools import wraps
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

class QuantException(Exception):
    """量化系统基础异常"""
    
    def __init__(self, message: str, code: str = "UNKNOWN_ERROR", details: Dict[str, Any] = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)


class DataSourceException(QuantException):
    """数据源异常"""
    pass


class TradingException(QuantException):
    """交易异常"""
    pass


class RiskException(QuantException):
    """风险异常"""
    pass


class BacktestException(QuantException):
    """回测异常"""
    pass


class ExceptionHandler:
    """异常处理器"""
    
    def __init__(self, sentry_dsn: Optional[str] = None):
        self.sentry_dsn = sentry_dsn
        
        if sentry_dsn:
            self._init_sentry()
    
    def _init_sentry(self):
        """初始化Sentry"""
        sentry_sdk.init(
            dsn=self.sentry_dsn,
            integrations=[
                FastApiIntegration(),
                SqlalchemyIntegration(),
            ],
            traces_sample_rate=1.0,
            environment="production",
            release="quant-strategy@1.0.0"
        )
    
    def handle_exception(self, exc: Exception, context: Dict[str, Any] = None):
        """处理异常"""
        context = context or {}
        
        # 记录到Sentry
        if self.sentry_dsn:
            with sentry_sdk.push_scope() as scope:
                for key, value in context.items():
                    scope.set_extra(key, value)
                sentry_sdk.capture_exception(exc)
        
        # 记录到日志
        logger.error(f"异常发生: {str(exc)}", exc_info=True, **context)
        
        # 发送告警（根据异常类型）
        self._send_alert(exc, context)
    
    def _send_alert(self, exc: Exception, context: Dict[str, Any]):
        """发送告警"""
        alert_level = self._determine_alert_level(exc)
        
        if alert_level in ["critical", "error"]:
            # 发送即时告警（如钉钉、Slack、邮件）
            self._send_immediate_alert(exc, context, alert_level)
        elif alert_level == "warning":
            # 记录到告警系统，稍后处理
            self._log_alert(exc, context)
    
    def _determine_alert_level(self, exc: Exception) -> str:
        """确定告警级别"""
        if isinstance(exc, (DataSourceException, RiskException)):
            return "critical"
        elif isinstance(exc, TradingException):
            return "error"
        elif isinstance(exc, BacktestException):
            return "warning"
        else:
            return "info"
    
    def _send_immediate_alert(self, exc: Exception, context: Dict[str, Any], level: str):
        """发送即时告警"""
        # 实际实现：发送到钉钉、Slack、邮件等
        alert_message = {
            "level": level,
            "exception": exc.__class__.__name__,
            "message": str(exc),
            "context": context,
            "timestamp": datetime.now().isoformat()
        }
        
        # 这里可以调用告警发送服务
        print(f"发送告警: {alert_message}")
    
    def _log_alert(self, exc: Exception, context: Dict[str, Any]):
        """记录告警"""
        # 记录到数据库或文件
        pass


# 异常处理装饰器
def exception_handler(func):
    """异常处理装饰器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # 获取上下文信息
            context = {
                "function": func.__name__,
                "module": func.__module__,
                "args": str(args),
                "kwargs": str(kwargs)
            }
            
            # 处理异常
            handler = ExceptionHandler()
            handler.handle_exception(e, context)
            
            # 重新抛出异常或返回默认值
            if isinstance(e, (DataSourceException, RiskException)):
                raise
            else:
                # 对于非关键异常，返回默认值
                return None
    
    return wrapper
```

### 4.2 告警集成配置

#### alert_integration.py
```python
import requests
import json
from typing import List, Dict, Any
from datetime import datetime


class AlertManager:
    """告警管理器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # 告警渠道
        self.channels = {
            'dingtalk': DingTalkChannel(config.get('dingtalk', {})),
            'slack': SlackChannel(config.get('slack', {})),
            'email': EmailChannel(config.get('email', {})),
            'sms': SMSChannel(config.get('sms', {}))
        }
    
    def send_alert(self, 
                   title: str, 
                   message: str, 
                   level: str = 'warning',
                   channels: List[str] = None,
                   details: Dict[str, Any] = None):
        """发送告警"""
        
        channels = channels or ['dingtalk']  # 默认使用钉钉
        
        alert_payload = {
            'title': title,
            'message': message,
            'level': level,
            'timestamp': datetime.now().isoformat(),
            'details': details or {}
        }
        
        # 发送到各个渠道
        results = []
        for channel_name in channels:
            if channel_name in self.channels:
                channel = self.channels[channel_name]
                try:
                    result = channel.send(alert_payload)
                    results.append({
                        'channel': channel_name,
                        'success': True,
                        'result': result
                    })
                except Exception as e:
                    results.append({
                        'channel': channel_name,
                        'success': False,
                        'error': str(e)
                    })
        
        return results


class DingTalkChannel:
    """钉钉告警渠道"""
    
    def __init__(self, config: Dict[str, Any]):
        self.webhook_url = config.get('webhook_url')
        self.secret = config.get('secret')
    
    def send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """发送钉钉告警"""
        
        # 构建消息
        message = {
            'msgtype': 'markdown',
            'markdown': {
                'title': f"{payload['level'].upper()}: {payload['title']}",
                'text': f"### {payload['title']}\n\n"
                       f"**级别**: {payload['level']}\n\n"
                       f"**时间**: {payload['timestamp']}\n\n"
                       f"**详情**: {payload['message']}\n\n"
                       f"**附加信息**: \n{json.dumps(payload['details'], indent=2, ensure_ascii=False)}"
            },
            'at': {
                'isAtAll': payload['level'] == 'critical'
            }
        }
        
        # 发送请求
        response = requests.post(self.webhook_url, json=message)
        response.raise_for_status()
        
        return response.json()


class SlackChannel:
    """Slack告警渠道"""
    
    def __init__(self, config: Dict[str, Any]):
        self.webhook_url = config.get('webhook_url')
        self.channel = config.get('channel', '#alerts')
    
    def send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """发送Slack告警"""
        
        # 颜色映射
        color_map = {
            'critical': '#ff0000',
            'error': '#ff6600',
            'warning': '#ffcc00',
            'info': '#3366ff'
        }
        
        # 构建消息
        message = {
            'channel': self.channel,
            'attachments': [{
                'color': color_map.get(payload['level'], '#cccccc'),
                'title': f"{payload['level'].upper()}: {payload['title']}",
                'text': payload['message'],
                'fields': [
                    {
                        'title': '级别',
                        'value': payload['level'],
                        'short': True
                    },
                    {
                        'title': '时间',
                        'value': payload['timestamp'],
                        'short': True
                    }
                ],
                'footer': '量化策略监控系统',
                'ts': datetime.now().timestamp()
            }]
        }
        
        # 添加详情
        if payload['details']:
            message['attachments'][0]['fields'].append({
                'title': '详情',
                'value': json.dumps(payload['details'], ensure_ascii=False),
                'short': False
            })
        
        # 发送请求
        response = requests.post(self.webhook_url, json=message)
        response.raise_for_status()
        
        return response.json()


# 配置示例
alert_config = {
    'dingtalk': {
        'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN',
        'secret': 'YOUR_SECRET'
    },
    'slack': {
        'webhook_url': 'https://hooks.slack.com/services/YOUR/WEBHOOK/URL',
        'channel': '#quant-alerts'
    },
    'email': {
        'smtp_server': 'smtp.gmail.com',
        'smtp_port': 587,
        'username': 'your-email@gmail.com',
        'password': 'your-password',
        'from_addr': 'your-email@gmail.com',
        'to_addrs': ['admin@example.com']
    }
}

# 全局告警管理器
alert_manager = AlertManager(alert_config)
```

---

## 五、部署脚本与自动化

### 5.1 部署脚本

#### deploy.sh
```bash
#!/bin/bash

set -e

# 配置
APP_NAME="quant-strategy"
REGISTRY="registry.example.com"
ENVIRONMENT="${ENVIRONMENT:-production}"
VERSION="${VERSION:-latest}"

echo "开始部署 ${APP_NAME} (环境: ${ENVIRONMENT}, 版本: ${VERSION})"

# 1. 构建Docker镜像
echo "1. 构建Docker镜像..."
docker build -t ${REGISTRY}/${APP_NAME}:${VERSION} .
docker tag ${REGISTRY}/${APP_NAME}:${VERSION} ${REGISTRY}/${APP_NAME}:latest

# 2. 推送镜像到仓库
echo "2. 推送镜像到仓库..."
docker push ${REGISTRY}/${APP_NAME}:${VERSION}
docker push ${REGISTRY}/${APP_NAME}:latest

# 3. 部署到Kubernetes
echo "3. 部署到Kubernetes..."
kubectl apply -f kubernetes/namespace.yaml
kubectl apply -f kubernetes/secrets.yaml
kubectl apply -f kubernetes/configmaps.yaml
kubectl apply -f kubernetes/deployment.yaml
kubectl apply -f kubernetes/service.yaml
kubectl apply -f kubernetes/ingress.yaml

# 4. 等待部署完成
echo "4. 等待部署完成..."
kubectl rollout status deployment/${APP_NAME} -n quant-system --timeout=300s

# 5. 运行健康检查
echo "5. 运行健康检查..."
sleep 30  # 等待应用完全启动
curl -f http://${APP_NAME}.example.com/health || {
    echo "健康检查失败!"
    exit 1
}

# 6. 发送部署通知
echo "6. 发送部署通知..."
curl -X POST -H "Content-Type: application/json" \
    -d '{"text": "'${APP_NAME}' v'${VERSION}' 已成功部署到 '${ENVIRONMENT}' 环境"}' \
    ${SLACK_WEBHOOK_URL}

echo "部署完成!"
```

### 5.2 自动化测试脚本

#### ci_cd_pipeline.yml
```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.12'
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements-dev.txt
        pip install pytest pytest-cov black flake8 mypy
    
    - name: Lint
      run: |
        black --check .
        flake8 .
        mypy --ignore-missing-imports .
    
    - name: Test
      run: |
        pytest --cov=. --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
        flags: unittests
  
  build:
    needs: test
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@v2
    
    - name: Login to DockerHub
      uses: docker/login-action@v2
      with:
        username: ${{ secrets.DOCKER_USERNAME }}
        password: ${{ secrets.DOCKER_PASSWORD }}
    
    - name: Build and push
      uses: docker/build-push-action@v4
      with:
        context: .
        push: true
        tags: |
          ${{ secrets.DOCKER_USERNAME }}/quant-strategy:latest
          ${{ secrets.DOCKER_USERNAME }}/quant-strategy:${{ github.sha }}
  
  deploy:
    needs: build
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Configure kubectl
      uses: azure/setup-kubectl@v3
      with:
        version: 'latest'
    
    - name: Deploy to Kubernetes
      run: |
        kubectl apply -f kubernetes/
        kubectl rollout status deployment/quant-strategy -n quant-system
      env:
        KUBECONFIG: ${{ secrets.KUBECONFIG }}
    
    - name: Run smoke tests
      run: |
        ./scripts/smoke_test.sh
    
    - name: Send notification
      run: |
        curl -X POST -H "Content-Type: application/json" \
          -d '{"text": "量化策略 v'${GITHUB_SHA}' 已成功部署到生产环境"}' \
          ${{ secrets.SLACK_WEBHOOK_URL }}
```

---

## 六、性能优化建议

### 6.1 数据库优化

1. **索引优化**
   ```sql
   -- 为常用查询字段创建索引
   CREATE INDEX idx_stock_date ON daily_prices(stock_code, trade_date);
   CREATE INDEX idx_factor_stock ON factor_scores(stock_code, calc_date);
   CREATE INDEX idx_backtest_result ON backtest_results(strategy_id, end_date);
   
   -- 分区表（按时间）
   CREATE TABLE daily_prices_2024 PARTITION OF daily_prices
   FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
   ```

2. **查询优化**
   ```python
   # 使用批量插入
   def bulk_insert_prices(prices_data):
       with engine.connect() as conn:
           conn.execute(
               daily_prices.insert(),
               prices_data
           )
       
   # 使用窗口函数替代循环
   def calculate_moving_average():
       query = """
       SELECT stock_code, trade_date, close,
              AVG(close) OVER (
                  PARTITION BY stock_code 
                  ORDER BY trade_date 
                  ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
              ) as ma_20
       FROM daily_prices
       """
   ```

### 6.2 缓存策略

1. **Redis缓存配置**
   ```python
   import redis
   from redis.exceptions import RedisError
   
   class CacheManager:
       def __init__(self):
           self.redis_client = redis.Redis(
               host='localhost',
               port=6379,
               db=0,
               decode_responses=True
           )
       
       def cache_data(self, key: str, data: Any, ttl: int = 3600):
           """缓存数据"""
           try:
               serialized = json.dumps(data)
               self.redis_client.setex(key, ttl, serialized)
           except RedisError as e:
               logger.warning(f"缓存失败: {e}")
       
       def get_cached_data(self, key: str) -> Optional[Any]:
           """获取缓存数据"""
           try:
               data = self.redis_client.get(key)
               if data:
                   return json.loads(data)
           except RedisError as e:
               logger.warning(f"获取缓存失败: {e}")
           return None
   ```

2. **缓存预热**
   ```python
   def warmup_cache():
       """缓存预热"""
       # 预热常用数据
       cache_keys = [
           'market_indices',
           'hot_stocks',
           'risk_metrics'
       ]
       
       for key in cache_keys:
           if not cache_manager.get_cached_data(key):
               data = fetch_data_for_key(key)
               cache_manager.cache_data(key, data, ttl=1800)
   ```

### 6.3 异步处理

1. **Celery任务队列**
   ```python
   from celery import Celery
   
   # 配置Celery
   celery_app = Celery(
       'quant_tasks',
       broker='redis://localhost:6379/0',
       backend='redis://localhost:6379/1'
   )
   
   @celery_app.task
   def run_backtest_task(strategy_id: str, start_date: str, end_date: str):
       """异步运行回测任务"""
       try:
           result = run_backtest(strategy_id, start_date, end_date)
           return result
       except Exception as e:
           logger.error(f"回测任务失败: {e}")
           raise
   
   @celery_app.task
   def update_stock_data_task():
       """异步更新股票数据"""
       stocks = get_all_stocks()
       for stock in stocks:
           update_single_stock_data(stock)
   ```

---

## 七、安全考虑

### 7.1 网络安全
- 使用TLS/SSL加密所有通信
- 配置防火墙规则，只开放必要端口
- 使用VPC私有网络
- 实施DDoS防护

### 7.2 数据安全
- 数据库加密存储
- 敏感数据加密传输
- 定期备份数据
- 实施访问控制和审计日志

### 7.3 应用安全
- 输入验证和参数化查询
- 防止SQL注入和XSS攻击
- API速率限制
- 使用API网关进行认证授权

### 7.4 监控安全
- 监控异常登录尝试
- 审计所有敏感操作
- 实时告警安全事件
- 定期安全扫描

---

## 八、维护与支持

### 8.1 日常维护任务
- 监控系统健康和性能
- 定期备份和恢复测试
- 更新依赖包和安全补丁
- 清理日志和临时文件

### 8.2 故障排除流程
1. **问题检测**: 监控告警触发
2. **问题诊断**: 查看日志和指标
3. **问题解决**: 根据预案采取措施
4. **问题复盘**: 分析根本原因，改进系统

### 8.3 灾难恢复
- 制定灾难恢复计划
- 定期进行恢复演练
- 多地域部署容灾
- 关键数据多地备份

---

*本方案为量化策略系统提供了完整的生产级部署与监控解决方案，确保系统的可靠性、可维护性和可扩展性。*