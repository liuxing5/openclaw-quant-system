# -*- coding: utf-8 -*-
"""FastAPI 入口：统一挂载 /api/v1/*，供 React 前端调用。

启动：
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir webapp/backend
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter
from fastapi.responses import JSONResponse

from .db import shutdown
from .routers import dashboard, market, system
from .warmup import start_warmup, stop_warmup

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
api.include_router(system.router)
api.include_router(dashboard.router)
api.include_router(market.router)
app.include_router(api)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)[:300], "path": request.url.path},
    )


@app.get("/", include_in_schema=False)
def root():
    return {"service": "quant-webapp", "api": "/api/v1", "docs": "/docs"}


@app.on_event("startup")
def _on_startup():
    start_warmup()


@app.on_event("shutdown")
def _on_shutdown():
    stop_warmup()
    try:
        shutdown()
    except Exception:  # noqa: BLE001
        pass
