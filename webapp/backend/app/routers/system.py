# -*- coding: utf-8 -*-
"""系统类接口：健康检查、公开设置、全局数据调度、数据源矩阵、当前用户。"""
import json
from datetime import datetime

from fastapi import APIRouter

from ..cache import ttl_cache
from ..db import execute, query, query_one, scalar

router = APIRouter(tags=["system"])

_DDL = """
CREATE TABLE IF NOT EXISTS app_settings (
    key VARCHAR(80) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""


def _ensure():
    try:
        execute(_DDL)
    except Exception:  # noqa: BLE001
        pass


_ensure()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _iso(v):
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def get_setting(key: str, default=None):
    row = query_one("SELECT value FROM app_settings WHERE key = %s", (key,))
    return row["value"] if row else default


def set_setting(key: str, value):
    execute(
        """
        INSERT INTO app_settings (key, value, updated_at) VALUES (%s, %s::jsonb, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """,
        (key, json.dumps(value, ensure_ascii=False)),
    )
    return value


def _safe_query(sql: str, params=()):
    try:
        from ..db import query

        return query(sql, params)
    except Exception:  # noqa: BLE001
        return []


@router.get("/health")
def health():
    try:
        db_ok = scalar("SELECT 1") == 1
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "db": "down", "detail": str(e)[:200], "time": _now()}
    return {"status": "ok" if db_ok else "error", "db": "ok" if db_ok else "down", "time": _now()}


@router.get("/settings/public")
def public_settings():
    saved = get_setting("site", {}) or {}
    return {
        "site_name": saved.get("site_name", "量化投研一体化平台"),
        "slogan": saved.get("slogan", "股票投研 · 数据一体化 · 本地数据驱动"),
        "announcement": saved.get("announcement", ""),
        "online_users": saved.get("online_users", 1),
        "first_register_bonus_days": saved.get("first_register_bonus_days", 1),
        "maintenance": bool(saved.get("maintenance", False)),
        "icp": saved.get("icp", ""),
        "updated_at": _now(),
    }


@router.get("/auth/me")
def auth_me():
    profile = get_setting("profile", {}) or {}
    return {
        "username": profile.get("username", "local"),
        "nickname": profile.get("nickname", "本地用户"),
        "email": profile.get("email", "local@localhost"),
        "role": profile.get("role", "owner"),
        "plan": profile.get("plan", "lifetime"),
        "plan_expiry": profile.get("plan_expiry", None),
        "quota_used": profile.get("quota_used", 0),
        "quota_total": profile.get("quota_total", 9999),
        "is_admin": True,
        "is_super_admin": True,
        "avatar": None,
        "created_at": profile.get("created_at", None),
    }


# ---------- 全局数据调度 ----------

DEFAULT_SCHEDULE = {
    "enabled": True,
    "timezone": "Asia/Shanghai",
    "jobs": [
        {
            "id": "daily_quotes",
            "name": "全市场日线行情",
            "cron": "30 18 * * 1-5",
            "enabled": True,
            "target": "core/market_data/quotes.py",
        },
        {
            "id": "boards",
            "name": "概念 / 行业板块快照",
            "cron": "45 18 * * 1-5",
            "enabled": True,
            "target": "webapp/scripts/sync_boards.py",
        },
        {
            "id": "index_quotes",
            "name": "主要指数日线",
            "cron": "50 18 * * 1-5",
            "enabled": True,
            "target": "webapp/scripts/sync_index_quotes.py",
        },
        {
            "id": "signals",
            "name": "快讯 / 研报 / 公告采集",
            "cron": "*/15 * * * *",
            "enabled": True,
            "target": "core feeds collector",
        },
    ],
}


@router.get("/dashboard/global-schedule")
def get_global_schedule():
    cfg = get_setting("global_schedule") or DEFAULT_SCHEDULE
    last_sync = {
        "daily_quotes": scalar("SELECT MAX(trade_date)::text FROM daily_quotes"),
        "boards": scalar("SELECT MAX(updated_at)::text FROM concept_board_quotes"),
        "index_quotes": scalar("SELECT MAX(trade_date)::text FROM index_quotes"),
        "signals": scalar("SELECT MAX(fetch_time)::text FROM raw_signals"),
    }
    items = []
    for job in cfg.get("jobs", []):
        sync_at = last_sync.get(job["id"])
        items.append(
            {
                **job,
                "last_success_at": sync_at,
                "status": "ok" if sync_at else "pending",
            }
        )
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "timezone": cfg.get("timezone", "Asia/Shanghai"),
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "jobs": items,
    }


@router.put("/dashboard/global-schedule")
def put_global_schedule(payload: dict):
    cfg = dict(get_setting("global_schedule") or DEFAULT_SCHEDULE)
    cfg.update(payload or {})
    set_setting("global_schedule", cfg)
    return get_global_schedule()


# ---------- 管理员数据源矩阵 ----------

PROVIDER_KINDS = [
    ("akshare", "公开免费行情 / 指数 / 板块", "无需 Key"),
    ("eastmoney", "板块资金流与龙虎榜镜像", "无需 Key"),
    ("ths", "同花顺行业板块与概念指数", "无需 Key"),
    ("local", "本地 PostgreSQL 历史数据底座", "直连"),
]


_LOCAL_DATASETS = [
    ("daily_quotes", "全市场日线", "daily_quotes", "trade_date"),
    ("stock_basic_info", "股票基础信息", "stock_basic_info", "list_date"),
    ("index_quotes", "主要指数日线", "index_quotes", "trade_date"),
    ("concept_board_quotes", "概念板块快照", "concept_board_quotes", "trade_date"),
    ("industry_board_quotes", "行业板块快照", "industry_board_quotes", "trade_date"),
    ("lhb_detail", "龙虎榜明细", "lhb_detail", "trade_date"),
    ("raw_signals", "快讯 / 研报 / 公告", "raw_signals", "fetch_time"),
    ("daily_candidates", "每日选股池", "daily_candidates", "snapshot_date"),
    ("hsgt_individual", "沪深港通个股", "hsgt_individual", "trade_date"),
    ("performance_tracking", "策略绩效跟踪", "performance_tracking", "rec_date"),
]


def _datasets() -> list[dict]:
    """一次 SQL 取全部数据集的「估算行数 + 最新日期」，避免 10 张表来回 20 次。

    本机到远端库单次往返约 0.25~0.6s，逐表查询会让 /system/data-sources 慢到 6s；
    合并成 UNION ALL 后固定 1 次往返。行数仍用 pg_class.reltuples 估算。
    """
    parts, params = [], []
    for ds_id, name, table, col in _LOCAL_DATASETS:
        parts.append(
            "SELECT %s::text AS id, %s::text AS name, "
            "(SELECT GREATEST(reltuples, 0)::bigint FROM pg_class WHERE oid = %s::regclass) AS rows, "
            f"(SELECT MAX({col})::text FROM {table}) AS latest"
        )
        params.extend([ds_id, name, table])
    rows = query(" UNION ALL ".join(parts), tuple(params))
    by_id = {r["id"]: r for r in rows}
    out = []
    for ds_id, name, table, _col in _LOCAL_DATASETS:
        r = by_id.get(ds_id)
        if not r:
            out.append({"id": ds_id, "name": name, "rows": 0, "latest": None, "status": "error"})
        else:
            out.append(
                {
                    "id": ds_id,
                    "name": name,
                    "rows": int(r["rows"] or 0),
                    "latest": r["latest"],
                    "status": "ok",
                }
            )
    return out


def _dataset(ds_id: str, name: str, table: str, date_col: str) -> dict:
    """单个本地数据集的规模与新鲜度（列缺失也不影响整体接口）。

    行数用规划器估算（pg_class.reltuples），避免对 200 万行的 daily_quotes
    做全表 COUNT(*) —— 实测一次 COUNT 就要数秒，而这里只是状态展示。
    """
    try:
        rows = scalar(
            "SELECT GREATEST(reltuples, 0)::bigint FROM pg_class WHERE oid = %s::regclass",
            (table,),
        )
        if not rows:
            rows = scalar(f"SELECT COUNT(*) FROM {table}") or 0
        latest = scalar(f"SELECT MAX({date_col})::text FROM {table}")
        return {"id": ds_id, "name": name, "rows": rows, "latest": latest, "status": "ok"}
    except Exception as e:  # noqa: BLE001
        return {
            "id": ds_id,
            "name": name,
            "rows": 0,
            "latest": None,
            "status": "error",
            "detail": str(e)[:160],
        }


@router.get("/system/data-sources")
@ttl_cache(60)
def data_sources():
    return _data_sources()


def _data_sources():
    rows = _safe_query(
        """
        SELECT id, name, category, tier, enabled, poll_interval_sec,
               last_success_at, last_error_at, last_error_msg, consecutive_failures
        FROM feed_sources ORDER BY tier, name
        """
    )
    feeds = [
        {
            "id": r["id"],
            "name": r["name"],
            "category": r["category"],
            "tier": r["tier"],
            "enabled": bool(r["enabled"]),
            "poll_interval_sec": r["poll_interval_sec"],
            "last_success_at": _iso(r["last_success_at"]),
            "last_error_at": _iso(r["last_error_at"]),
            "last_error_msg": (r["last_error_msg"] or "")[:200],
            "consecutive_failures": r["consecutive_failures"] or 0,
            "status": "ok"
            if r["enabled"] and (r["consecutive_failures"] or 0) == 0
            else ("error" if r["consecutive_failures"] else "disabled"),
        }
        for r in rows
    ]

    try:
        local_datasets = _datasets()
    except Exception:  # noqa: BLE001 单表缺失时逐表降级，接口不整体失败
        local_datasets = [
            _dataset(ds_id, name, table, col) for ds_id, name, table, col in _LOCAL_DATASETS
        ]

    return {
        "providers": [
            {
                "id": pid,
                "name": pid,
                "kind": desc,
                "auth": auth,
                "enabled": True,
                "status": "ok",
            }
            for pid, desc, auth in PROVIDER_KINDS
        ],
        "note": "当前使用公共免费数据源，无需 Key",
        "feeds": feeds,
        "datasets": local_datasets,
        "updated_at": _now(),
    }
