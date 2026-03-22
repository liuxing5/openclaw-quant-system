# Quant System Dockerfile
# 企业级量化交易系统容器镜像

# 第一阶段：构建阶段
FROM python:3.12-slim AS builder

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    git \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 创建Python虚拟环境
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 安装Python依赖
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY quant_system/ /app/quant_system/
COPY config/ /app/config/
COPY scripts/ /app/scripts/

# 第二阶段：运行阶段
FROM python:3.12-slim

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production \
    PORT=5000

# 安装运行时系统依赖
RUN apt-get update && apt-get install -y \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 创建非root用户
RUN groupadd -r quant && useradd -r -g quant -s /bin/bash quant

# 从构建阶段复制Python虚拟环境
COPY --from=builder /opt/venv /opt/venv

# 创建应用目录
WORKDIR /app

# 复制应用代码
COPY --chown=quant:quant quant_system/ /app/quant_system/
COPY --chown=quant:quant config/ /app/config/
COPY --chown=quant:quant scripts/ /app/scripts/
COPY --chown=quant:quant requirements.txt /app/

# 创建数据目录
RUN mkdir -p /app/data /app/logs /app/stats /app/auth_db && \
    chown -R quant:quant /app

# 切换到非root用户
USER quant

# 暴露端口
EXPOSE 5000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

# 启动命令
CMD ["python", "-m", "quant_system.web_dashboard.app_integrated"]