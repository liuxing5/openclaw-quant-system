# 量化投研平台：前端构建 + FastAPI 运行时
FROM node:20-slim AS frontend
WORKDIR /app/webapp/frontend
COPY webapp/frontend/package.json webapp/frontend/package-lock.json ./
RUN npm ci
COPY webapp/frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY webapp/backend/requirements.txt /app/webapp/backend/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/webapp/backend/requirements.txt

COPY core /app/core
COPY webapp/backend /app/webapp/backend
COPY --from=frontend /app/webapp/frontend/dist /app/webapp/frontend/dist

ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5)" || exit 1

CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --app-dir webapp/backend"]
