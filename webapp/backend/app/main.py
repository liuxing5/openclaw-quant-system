# -*- coding: utf-8 -*-
"""FastAPI 入口：统一挂载 /api/v1/*，并可直接托管构建后的 React 前端。

启动：
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir webapp/backend

前端两种访问方式：
    * 开发：cd webapp/frontend && npm run dev  →  http://127.0.0.1:5173/dashboard
    * 生产：npm run build 后，本服务直接托管 dist  →  http://127.0.0.1:8000/dashboard
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRouter

from .db import shutdown
from .db_schema import init_schema
from .routers import (
    admin,
    analysis,
    anomaly,
    auth,
    content,
    dashboard,
    feedback,
    finance,
    market,
    portfolio,
    realtime,
    review,
    simulation,
    strategy,
    system,
)
from .warmup import start_warmup, stop_warmup

# webapp/frontend/dist（app/main.py -> app -> backend -> webapp）
DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(
    title="量化投研一体化平台",
    description="本地 PostgreSQL 数据底座驱动的股票投研看板",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api/v1")
api.include_router(auth.router)
api.include_router(system.router)
api.include_router(dashboard.router)
api.include_router(market.router)
api.include_router(analysis.router)
api.include_router(realtime.router)
api.include_router(portfolio.router)
api.include_router(simulation.router)
api.include_router(strategy.router)
api.include_router(review.router)
api.include_router(anomaly.router)
api.include_router(feedback.router)
api.include_router(content.router)
api.include_router(finance.router)
api.include_router(admin.router)
app.include_router(api)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)[:300], "path": request.url.path},
    )


@app.on_event("startup")
def _on_startup():
    try:
        init_schema()
    except Exception:  # noqa: BLE001 建表失败不阻断启动
        pass
    start_warmup()


@app.on_event("shutdown")
def _on_shutdown():
    stop_warmup()
    try:
        shutdown()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# SPA 托管：/api 之外的路径全部回退到 index.html，交给前端路由处理
# ---------------------------------------------------------------------------
# Windows 注册表常把 .js 的 Content-Type 记成 text/plain，浏览器会拒绝执行
# type="module" 脚本；显式给出前端产物的 MIME，避免整页白屏。
_MIME = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json",
}


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith(("api/", "docs", "openapi.json", "redoc")):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if DIST_DIR.is_dir():
        index = DIST_DIR / "index.html"
        target = (DIST_DIR / full_path).resolve() if full_path else index
        try:
            inside = target.is_relative_to(DIST_DIR.resolve())
        except AttributeError:  # Python < 3.9
            inside = str(target).startswith(str(DIST_DIR.resolve()))
        if inside and target.is_file():
            return FileResponse(target, media_type=_MIME.get(target.suffix.lower()))
        if index.is_file():
            return FileResponse(index, media_type="text/html")
    return JSONResponse(
        {
            "service": "quant-webapp",
            "api": "/api/v1",
            "docs": "/docs",
            "hint": "前端尚未构建：cd webapp/frontend && npm run build",
        },
        status_code=200,
    )
