# -*- coding: utf-8 -*-
"""D3 业务实现：菜单 / 权限矩阵 / 本地用户 / 审计日志 / 运行监控 / 工作台 /
个人资料 / 管理端全量设置。

为什么这些常量放服务层
----------------------
1. ``/permissions/menus`` 与 ``/system/permissions`` 必须严格对齐同一份菜单结构
   （权限项「对照菜单树」），两处各写一份迟早漂移，所以菜单树只声明一次，
   权限矩阵由菜单树推导 + 少量功能权限补充。
2. 本系统是**单用户本地版**，没有真实的多租户/学员/订单业务；这些接口需要
   「结构完整可用」的演示数据（``meta.seed`` 标注），集中声明便于一眼看出哪些是假数据。
3. 路由层只做参数绑定，便于用 TestClient 单测每个端点。
"""
from __future__ import annotations

import copy
import json
import platform as _platform
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from ..cache import cache_stats, ttl_cache
from ..db import pool_stats
from ..routers.system import _safe_query, get_setting, set_setting

# ---------- 通用工具 ----------


def _iso(v):
    """date/datetime/Decimal → JSON 可序列化值（契约要求时间用 ISO 字符串）。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def jsonify(o):
    """整棵响应树递归转成可 JSON 序列化的结构（兜底，防止漏掉嵌套类型）。"""
    if isinstance(o, dict):
        return {str(k): jsonify(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonify(v) for v in o]
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, bytes):
        return o.decode("utf-8", "replace")
    if isinstance(o, set):
        return [jsonify(v) for v in o]
    return o


def mask_email(value) -> str:
    """邮箱打码：``liuxing1187891@gmail.com`` → ``l***@gmail.com``。

    为什么：本系统虽然只有本地管理员，但用户列表/审计日志/反馈都可能带邮箱，
    脱敏是展示层的硬要求，统一在这里做，避免各接口各写一遍。
    """
    if not value:
        return ""
    s = str(value)
    if "@" not in s:
        return (s[:1] + "***") if s else s
    local, _, domain = s.partition("@")
    return f"{local[:1]}***@{domain}"


def _clamp(page: int, size: int, max_size: int = 100):
    page = max(1, int(page or 1))
    size = min(max(1, int(size or 20)), max_size)
    return page, size, (page - 1) * size


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- 菜单树（唯一声明处） ----------

#: 全站菜单树。对照 ``spec/pages/_index.md`` 的路由一览，覆盖
#: 总台/自选/持仓/模拟盘/策略/个股分析/连板梯队/龙虎榜/概念分析/行业分析/
#: 异动/复盘/学院/资讯/外围/数据/指数/财务/环境/系统/工作台/设置/个人中心。
MENU_TREE: list[dict] = [
    {"key": "dashboard", "path": "/dashboard", "title": "总台", "icon": "dashboard", "children": []},
    {"key": "watchlist", "path": "/watchlist", "title": "自选", "icon": "star", "children": []},
    {
        "key": "holdings",
        "path": "/holdings",
        "title": "持仓",
        "icon": "briefcase",
        "children": [
            {"key": "holdings:list", "path": "/holdings", "title": "持仓列表", "icon": "list", "children": []},
            {"key": "holdings:alerts", "path": "/holdings/alerts", "title": "持仓提醒", "icon": "bell", "children": []},
        ],
    },
    {
        "key": "simulation",
        "path": "/simulation/account",
        "title": "模拟盘",
        "icon": "flask",
        "children": [
            {"key": "simulation:account", "path": "/simulation/account", "title": "模拟账户", "icon": "wallet", "children": []},
            {"key": "simulation:orders", "path": "/simulation/orders", "title": "委托记录", "icon": "scroll", "children": []},
        ],
    },
    {"key": "strategy", "path": "/strategy", "title": "策略", "icon": "cog", "children": []},
    {"key": "stock-analysis", "path": "/stock-analysis", "title": "个股分析", "icon": "chart", "children": []},
    {"key": "limit-ladder", "path": "/limit-ladder", "title": "连板梯队", "icon": "layers", "children": []},
    {"key": "dragon-tiger", "path": "/dragon-tiger", "title": "龙虎榜", "icon": "fire", "children": []},
    {"key": "concept-analysis", "path": "/concept-analysis", "title": "概念分析", "icon": "hexagon", "children": []},
    {"key": "industry-analysis", "path": "/industry-analysis", "title": "行业分析", "icon": "factory", "children": []},
    {
        "key": "anomaly",
        "path": "/anomaly",
        "title": "异动",
        "icon": "activity",
        "children": [
            {"key": "anomaly:realtime", "path": "/anomaly", "title": "实时异动", "icon": "zap", "children": []},
            {"key": "anomaly:rules", "path": "/anomaly/rules", "title": "异动规则", "icon": "sliders", "children": []},
        ],
    },
    {"key": "review", "path": "/review", "title": "复盘", "icon": "clipboard", "children": []},
    {
        "key": "academy",
        "path": "/academy/basics",
        "title": "学院",
        "icon": "book",
        "children": [
            {"key": "academy:basics", "path": "/academy/basics", "title": "基础入门", "icon": "book-open", "children": []},
            {"key": "academy:books", "path": "/academy/books", "title": "经典书单", "icon": "library", "children": []},
        ],
    },
    {
        "key": "info",
        "path": "/info/news-flash",
        "title": "资讯",
        "icon": "rss",
        "children": [
            {"key": "info:news-flash", "path": "/info/news-flash", "title": "每日快讯", "icon": "newspaper", "children": []},
            {"key": "info:hot-search", "path": "/info/hot-search", "title": "全网热搜", "icon": "trending", "children": []},
            {"key": "info:fund-flow", "path": "/info/fund-flow", "title": "资金流向", "icon": "dollar", "children": []},
        ],
    },
    {
        "key": "global",
        "path": "/global-index",
        "title": "外围",
        "icon": "globe",
        "children": [
            {"key": "global:index", "path": "/global-index", "title": "外围指数", "icon": "globe", "children": []},
            {"key": "global:commodity", "path": "/oil-gold", "title": "全球商品", "icon": "droplet", "children": []},
            {"key": "global:merger", "path": "/merger-restructuring", "title": "并购重组", "icon": "merge", "children": []},
        ],
    },
    {
        "key": "data",
        "path": "/data/quotes",
        "title": "数据",
        "icon": "database",
        "children": [
            {"key": "data:quotes", "path": "/data/quotes", "title": "全市场行情", "icon": "table", "children": []},
            {"key": "data:fundamentals", "path": "/data/fundamentals", "title": "基本面数据", "icon": "file", "children": []},
        ],
    },
    {
        "key": "index",
        "path": "/index",
        "title": "指数",
        "icon": "bar-chart",
        "children": [
            {"key": "index:summary", "path": "/index", "title": "指数总览", "icon": "line-chart", "children": []},
            {"key": "index:components", "path": "/index/components", "title": "指数成分", "icon": "pie-chart", "children": []},
        ],
    },
    {
        "key": "finance",
        "path": "/finance/statements",
        "title": "财务",
        "icon": "file-text",
        "children": [
            {"key": "finance:statements", "path": "/finance/statements", "title": "财务报表", "icon": "file-text", "children": []},
            {"key": "finance:indicators", "path": "/finance/indicators", "title": "财务指标", "icon": "calculator", "children": []},
        ],
    },
    {
        "key": "market-env",
        "path": "/market-env",
        "title": "环境",
        "icon": "cloud",
        "children": [
            {"key": "market-env:today", "path": "/market-env", "title": "市场环境", "icon": "sun", "children": []},
            {"key": "market-env:history", "path": "/market-env/history", "title": "环境历史", "icon": "history", "children": []},
        ],
    },
    {
        "key": "system",
        "path": "/system/users",
        "title": "系统",
        "icon": "shield",
        "children": [
            {"key": "system:users", "path": "/system/users", "title": "用户管理", "icon": "users", "children": []},
            {"key": "system:permissions", "path": "/system/permissions", "title": "权限点", "icon": "key", "children": []},
            {"key": "system:audit-logs", "path": "/system/audit-logs", "title": "审计日志", "icon": "history", "children": []},
        ],
    },
    {
        "key": "workbench",
        "path": "/workbench/monitor",
        "title": "工作台",
        "icon": "monitor",
        "children": [
            {"key": "workbench:monitor", "path": "/workbench/monitor", "title": "运行监控", "icon": "heart", "children": []},
            {"key": "workbench:students", "path": "/workbench/students", "title": "学员管理", "icon": "users", "children": []},
            {"key": "workbench:payments", "path": "/workbench/payments", "title": "订单管理", "icon": "credit-card", "children": []},
            {"key": "workbench:feedbacks", "path": "/workbench/feedbacks", "title": "反馈处理", "icon": "message", "children": []},
        ],
    },
    {
        "key": "settings",
        "path": "/settings/general",
        "title": "设置",
        "icon": "settings",
        "children": [
            {"key": "settings:general", "path": "/settings/general", "title": "通用设置", "icon": "sliders", "children": []},
            {"key": "settings:ai", "path": "/settings/ai", "title": "AI 设置", "icon": "cpu", "children": []},
            {"key": "settings:data-sources", "path": "/settings/data-sources", "title": "数据源设置", "icon": "plug", "children": []},
        ],
    },
    {"key": "profile", "path": "/profile", "title": "个人中心", "icon": "user", "children": []},
]

#: 本系统对外角色（单用户本地版只有 admin 实权，其余角色保留给权限矩阵展示）。
ROLES_FOR_MENUS = ["admin", "user"]
ROLES = ["admin", "editor", "user"]

#: 管理专属菜单组：editor 不可见（系统 / 工作台 / 设置 三个后台分区）。
_ADMIN_GROUPS = ("system", "workbench", "settings")

#: 普通用户（学员）可见菜单组 —— 对齐原站 profile.md 里 student 角色真实权限清单，
#: 原站学员确实无 持仓/模拟盘/指数/财务/数据/环境/后台 权限。
_USER_GROUPS = (
    "dashboard", "watchlist", "strategy", "stock-analysis", "limit-ladder",
    "dragon-tiger", "concept-analysis", "industry-analysis", "anomaly", "review",
    "academy", "info", "global", "profile",
)


def _in_groups(key: str, groups) -> bool:
    """key 是否属于某组：组本身或组的子项（``system`` / ``system:users``）。"""
    return key in groups or any(key.startswith(f"{g}:") for g in groups)


def _walk(nodes):
    """深度优先展开菜单树（权限矩阵要扁平化）。"""
    for n in nodes:
        yield n
        if n.get("children"):
            yield from _walk(n["children"])


def menus() -> dict:
    """当前用户可见菜单：本系统全量放行，返回整棵菜单树。"""
    return {
        "items": copy.deepcopy(MENU_TREE),
        "roles": list(ROLES_FOR_MENUS),
        "meta": {"local": True, "note": "单用户本地版：当前账号可见全部菜单"},
    }


#: 非菜单型功能权限（写操作 / 后台读取），与菜单权限一起构成完整矩阵。
_EXTRA_PERMISSIONS = [
    {
        "key": "settings:manage",
        "label": "全量设置读写",
        "desc": "读取与批量写入 /settings/all 管理端设置（共享状态）",
        "grants": {"admin": True, "editor": False, "user": False},
    },
    {
        "key": "feedback:reply",
        "label": "反馈回复",
        "desc": "回复全部用户的反馈并变更其状态",
        "grants": {"admin": True, "editor": True, "user": False},
    },
    {
        "key": "feedback:submit",
        "label": "反馈提交",
        "desc": "提交自己的反馈",
        "grants": {"admin": True, "editor": True, "user": True},
    },
    {
        "key": "user:manage",
        "label": "用户管理",
        "desc": "查看本地用户列表（邮箱脱敏）",
        "grants": {"admin": True, "editor": False, "user": False},
    },
    {
        "key": "audit:view",
        "label": "审计日志查看",
        "desc": "查看认证事件与本系统操作日志",
        "grants": {"admin": True, "editor": False, "user": False},
    },
    {
        "key": "monitor:view",
        "label": "运行监控",
        "desc": "查看连接池、缓存、数据源与定时任务健康度",
        "grants": {"admin": True, "editor": False, "user": False},
    },
    {
        "key": "profile:update",
        "label": "个人资料维护",
        "desc": "查看与更新个人资料及偏好设置",
        "grants": {"admin": True, "editor": True, "user": True},
    },
]


def permission_matrix() -> dict:
    """角色-权限矩阵；权限项由菜单树推导，保证与 ``/permissions/menus`` 对齐。"""
    perms = []
    for node in _walk(MENU_TREE):
        key = node["key"]
        perms.append(
            {
                "key": f"menu:{key}",
                "label": node["title"],
                "desc": f"访问「{node['title']}」页面（{node['path']}）",
                "grants": {
                    "admin": True,
                    "editor": not _in_groups(key, _ADMIN_GROUPS),
                    "user": _in_groups(key, _USER_GROUPS),
                },
            }
        )
    perms.extend(copy.deepcopy(_EXTRA_PERMISSIONS))
    return {
        "roles": list(ROLES),
        "permissions": perms,
        "meta": {"local": True, "note": "菜单权限由 /permissions/menus 的菜单树推导"},
    }


# ---------- 本地用户 ----------

_FALLBACK_ID_SEED = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def _auth_user_row() -> dict:
    """``auth.users`` 第一行（本机 Supabase 可能没有用户，取不到返回 {}）。"""
    rows = _safe_query(
        """
        SELECT id, email, role, created_at, last_sign_in_at, raw_user_meta_data, is_super_admin
        FROM auth.users
        WHERE deleted_at IS NULL
        ORDER BY created_at
        LIMIT 1
        """
    )
    return rows[0] if rows else {}


def _local_admin_item() -> dict:
    """本地管理员账号：优先真实 ``auth.users``，否则用 /auth/me 的 profile 兜底。

    为什么兜底：本系统是单用户本地版，Supabase ``auth.users`` 常常是空表，
    用户列表接口仍需返回一条结构完整的管理员记录。
    """
    row = _auth_user_row()
    if row:
        meta = row.get("raw_user_meta_data") or {}
        email = row.get("email") or meta.get("email") or "local@localhost"
        username = meta.get("username") or str(email).split("@")[0] or "local"
        return {
            "id": _iso(row.get("id")),
            "username": username,
            "nickname": meta.get("nickname") or username,
            "email_masked": mask_email(email),
            "role": row.get("role") or "admin",
            "status": "active",
            "is_super_admin": bool(row.get("is_super_admin")),
            "created_at": _iso(row.get("created_at")),
            "last_login_at": _iso(row.get("last_sign_in_at")),
            "source": "auth.users",
        }

    # 兜底：复用 /auth/me 的 profile（同一份数据源，避免两处口径不一致）
    from ..routers.system import auth_me

    me = auth_me(None)
    email = me.get("email") or "local@localhost"
    username = me.get("username") or "local"
    stable_id = uuid.uuid5(_FALLBACK_ID_SEED, str(email))  # 每次调用同一 id，前端可用作 key
    return {
        "id": str(stable_id),
        "username": username,
        "nickname": me.get("nickname") or username,
        "email_masked": mask_email(email),
        "role": me.get("role") or "admin",
        "status": "active",
        "is_super_admin": bool(me.get("is_super_admin")),
        "created_at": _iso(me.get("created_at")),
        "last_login_at": None,
        "source": "auth/me.profile",
    }


def local_users(page: int, size: int, keyword: str = "") -> dict:
    """本地用户列表（分页 + 关键字 + 邮箱脱敏）。"""
    page, size, offset = _clamp(page, size)
    item = _local_admin_item()
    kw = (keyword or "").strip().lower()
    items = [item]
    if kw:
        hay = " ".join(
            str(item.get(f) or "") for f in ("username", "nickname", "email_masked", "role")
        ).lower()
        items = [item] if kw in hay else []
    total = len(items)
    return {
        "items": items[offset : offset + size],
        "total": total,
        "page": page,
        "size": size,
        "meta": {
            "local": True,
            "source": item["source"],
            "note": "单用户本地版：仅一个管理员账号，邮箱已脱敏",
        },
    }


# ---------- 审计日志 ----------

#: Supabase ``auth.audit_log_entries`` 的 payload 字段名在不同版本不一致，
#: 这里按优先级兜底取值，取不到就用 ``auth.event``。
_PAYLOAD_ACTION_KEYS = ("event", "event_type", "type", "action", "operation")
_PAYLOAD_ACTOR_KEYS = (
    "actor_email", "actor_username", "actor", "email", "user_email", "username",
)
_PAYLOAD_TARGET_KEYS = ("path", "route", "target", "resource", "provider", "strategy")


def _auth_audit_item(row: dict) -> dict:
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:  # noqa: BLE001
            payload = {"raw": payload[:200]}
    if not isinstance(payload, dict):
        payload = {"raw": str(payload)[:200]}

    action = next((payload[k] for k in _PAYLOAD_ACTION_KEYS if payload.get(k)), "auth.event")
    actor = next((payload[k] for k in _PAYLOAD_ACTOR_KEYS if payload.get(k)), None)
    if not actor:
        actor = payload.get("actor_id") or payload.get("user_id") or "system"
    target = next((payload[k] for k in _PAYLOAD_TARGET_KEYS if payload.get(k)), None)
    if not target:
        uid = payload.get("user_id") or payload.get("actor_id")
        target = str(uid)[:8] if uid else "-"
    actor = str(actor)
    if "@" in actor:  # 审计里的邮箱同样脱敏
        actor = mask_email(actor)
    return {
        "id": _iso(row.get("id")),
        "actor": actor,
        "action": str(action),
        "target": str(target)[:120],
        "ip": row.get("ip_address") or "-",
        "created_at": _iso(row.get("created_at")),
    }


def _derived_audit_rows(actor: str, limit: int = 200) -> list[dict]:
    """降级来源：本系统「操作痕迹」合成的审计条目。

    为什么合成：``auth.audit_log_entries`` 可能不存在或为空（本机实测 0 行），
    审计页不能白屏，于是用 app_settings 的更新时间 + feed_sources 的错误日志
    拼出真实发生过的事件。
    """
    out: list[dict] = []
    for r in _safe_query(
        "SELECT key, updated_at FROM app_settings WHERE updated_at IS NOT NULL "
        "ORDER BY updated_at DESC LIMIT %s",
        (limit,),
    ):
        out.append(
            {
                "id": f"setting:{r['key']}",
                "actor": "local-admin",
                "action": "settings.update",
                "target": str(r["key"]),
                "ip": "127.0.0.1",
                "created_at": _iso(r.get("updated_at")),
            }
        )
    for r in _safe_query(
        "SELECT id, name, last_error_at, last_error_msg FROM feed_sources "
        "WHERE last_error_at IS NOT NULL ORDER BY last_error_at DESC LIMIT %s",
        (limit,),
    ):
        out.append(
            {
                "id": f"feed:{r['id']}",
                "actor": "scheduler",
                "action": "feed.error",
                "target": str(r["name"])[:80],
                "ip": "-",
                "created_at": _iso(r.get("last_error_at")),
                "detail": (r.get("last_error_msg") or "")[:160],
            }
        )
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    kw = (actor or "").strip().lower()
    if kw:
        out = [
            r
            for r in out
            if kw in " ".join(str(r.get(f) or "") for f in ("actor", "action", "target")).lower()
        ]
    return out[:limit]


def _derived_seed_rows() -> list[dict]:
    """连真实痕迹都没有时的演示条目（``meta.seed`` 明确标注，绝不冒充真实日志）。"""
    base = datetime.now(timezone.utc)
    spec = [
        ("local-admin", "settings.update", "general.platform_name", "127.0.0.1", 5),
        ("scheduler", "feed.success", "AKShare-龙虎榜", "-", 12),
        ("local-admin", "profile.login", "local", "127.0.0.1", 40),
        ("scheduler", "strategy.scan", "limit_up_breakout", "-", 96),
        ("local-admin", "settings.update", "ai.enabled", "127.0.0.1", 180),
        ("scheduler", "funnel.run", "daily_funnel", "-", 300),
    ]
    return [
        {
            "id": f"demo:{i + 1}",
            "actor": a,
            "action": act,
            "target": tgt,
            "ip": ip,
            "created_at": (base - timedelta(minutes=m)).isoformat(timespec="seconds"),
        }
        for i, (a, act, tgt, ip, m) in enumerate(spec)
    ]


def audit_logs(page: int, size: int, actor: str = "") -> dict:
    """审计日志：优先 ``auth.audit_log_entries``（SQL 分页下推），空则降级合成。

    为什么用 ``COUNT(*) OVER()``：本机到远端库一次往返约 0.25s，
    total + page 合成一条查询能省掉整整一次往返。
    """
    page, size, offset = _clamp(page, size)
    kw = (actor or "").strip()
    pattern = f"%{kw}%"
    where = "WHERE (payload::text ILIKE %s OR COALESCE(ip_address,'') ILIKE %s)" if kw else ""
    params = (pattern, pattern) if kw else ()

    # 这条 COUNT 同时承担「表是否存在 + 是否有数据」的探测：
    # 表不存在/无权限时 _safe_query 返回 [] → 自动走降级分支。
    probe = _safe_query("SELECT COUNT(*)::int AS c FROM auth.audit_log_entries")
    if probe and (probe[0]["c"] or 0) > 0:
        rows = _safe_query(
            f"""
            SELECT COUNT(*) OVER() AS total, id, payload, created_at, ip_address
            FROM auth.audit_log_entries
            {where}
            ORDER BY created_at DESC NULLS LAST
            LIMIT %s OFFSET %s
            """,
            (*params, size, offset),
        )
        total = int(rows[0]["total"]) if rows else int(probe[0]["c"] or 0)
        return {
            "items": [_auth_audit_item(r) for r in rows],
            "total": total,
            "page": page,
            "size": size,
            "meta": {"source": "auth", "table": "auth.audit_log_entries"},
        }

    # 降级：本系统操作日志合成（量小，取前 200 条在内存里排序分页）
    merged = _derived_audit_rows(kw)
    seed = False
    if not merged and not kw:
        merged = _derived_seed_rows()
        seed = True
    total = len(merged)
    return {
        "items": merged[offset : offset + size],
        "total": total,
        "page": page,
        "size": size,
        "meta": {
            "source": "derived",
            "seed": seed,
            "note": "auth.audit_log_entries 无数据，改由 app_settings 更新 + feed_sources 错误日志合成"
            if not seed
            else "无任何审计数据，返回演示条目（seed=true）",
        },
    }


# ---------- 运行监控 ----------

#: 一条 SQL 拿齐监控所需的全部聚合：本机到远端库一次往返约 0.25s，
#: 拆成 6 条查询就是 1.5s，监控页没必要付出这个代价（性能铁律）。
_MONITOR_SQL = """
SELECT
  (SELECT COUNT(*) FROM feed_sources)::int AS feed_total,
  (SELECT COUNT(*) FROM feed_sources WHERE enabled)::int AS feed_enabled,
  (SELECT COUNT(*) FROM feed_sources WHERE NOT enabled)::int AS feed_disabled,
  (SELECT COUNT(*) FROM feed_sources WHERE enabled AND COALESCE(consecutive_failures,0) > 0)::int AS feed_failed,
  (SELECT MAX(last_success_at) FROM feed_sources) AS feed_last_success,
  (SELECT MAX(last_error_at) FROM feed_sources) AS feed_last_error,
  (SELECT MAX(created_at) FROM strategy_scans) AS scan_last,
  (SELECT COUNT(*) FROM strategy_scans)::int AS scan_total,
  (SELECT MAX(created_at) FROM funnel_results) AS funnel_last,
  (SELECT COUNT(*) FROM funnel_results)::int AS funnel_total,
  (SELECT elapsed_seconds FROM funnel_results ORDER BY created_at DESC LIMIT 1) AS funnel_elapsed,
  (SELECT MAX(pushed_at) FROM push_history) AS push_last,
  (SELECT COUNT(*) FROM push_history)::int AS push_total,
  (SELECT MAX(created_at) FROM app_strategy_runs) AS run_last,
  (SELECT COALESCE(AVG(elapsed_ms),0)::int FROM app_strategy_runs) AS run_avg_ms,
  (SELECT COUNT(*) FROM app_strategy_runs)::int AS run_total
"""


def _age_sec(ts) -> int | None:
    if not isinstance(ts, datetime):
        return None
    aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - aware).total_seconds()))


def _job(name: str, last_run, duration_ms=None, stale_hours: int = 24, source: str = "") -> dict:
    """由业务表的最新时间戳推断一个后台任务的运行状态。

    为什么这样推断：本机没有真正的调度器执行记录可查，业务表的时间戳就是
    任务「最近一次真的跑过」的唯一可信证据。
    """
    age = _age_sec(last_run)
    if last_run is None:
        status = "pending"
    elif age is not None and age > stale_hours * 3600:
        status = "stale"
    else:
        status = "ok"
    return {
        "name": name,
        "last_run": _iso(last_run),
        "status": status,
        "duration_ms": int(duration_ms) if duration_ms is not None else None,
        "age_sec": age,
        "source": source,
    }


@ttl_cache(60)
def monitor() -> dict:
    """运行监控：连接池 / 缓存 / 数据源 / 调度 / 任务 / 接口健康 / 系统信息。

    性能注意：``MAX(daily_quotes.trade_date)`` 在 200 万行上是一次全表扫描（实测 20s+），
    这里改读 market 服务的 30s 缓存（warmup 每轮会刷新它），本函数再缓存 60s。
    """
    rows = _safe_query(_MONITOR_SQL)
    st = rows[0] if rows else {}
    degraded = not bool(st)

    def g(k, default=0):
        return st.get(k, default)

    failed = int(g("feed_failed"))
    data_sources = {
        "total": int(g("feed_total")),
        "enabled": int(g("feed_enabled")),
        "disabled": int(g("feed_disabled")),
        "failed": failed,
        "last_success_at": _iso(g("feed_last_success")),
        "last_error_at": _iso(g("feed_last_error")),
    }

    # 调度配置：只读 app_settings，缺省回落到 system.py 的内置默认值
    from ..routers.system import DEFAULT_SCHEDULE

    saved_cfg = get_setting("global_schedule")  # 只读一次：本机到远端库一次往返约 0.25s
    cfg = saved_cfg or DEFAULT_SCHEDULE
    schedule = {
        "enabled": bool(cfg.get("enabled", True)),
        "timezone": cfg.get("timezone", "Asia/Shanghai"),
        "source": "app_settings" if saved_cfg else "default",
        "jobs": [
            {
                "id": j.get("id"),
                "name": j.get("name"),
                "cron": j.get("cron"),
                "enabled": bool(j.get("enabled", True)),
                "target": j.get("target"),
            }
            for j in cfg.get("jobs", [])
        ],
    }

    elapsed = st.get("funnel_elapsed")  # 注意：不是 g()，降级时要保留 None 而不是 0
    jobs = [
        _job("数据源采集（feed_sources）", st.get("feed_last_success"), None, 24, "feed_sources"),
        _job("策略扫描（strategy_scans）", st.get("scan_last"), None, 24, "strategy_scans"),
        _job(
            "漏斗选股（funnel_results）",
            st.get("funnel_last"),
            int(float(elapsed) * 1000) if elapsed is not None else None,
            24,
            "funnel_results",
        ),
        _job("策略跑批（app_strategy_runs）", st.get("run_last"), st.get("run_avg_ms"), 24, "app_strategy_runs"),
        _job("消息推送（push_history）", st.get("push_last"), None, 24, "push_history"),
    ]
    if failed:
        jobs[0]["status"] = "error"  # 有连续失败的数据源，采集任务直接判红
    jobs[0]["detail"] = f"数据源 {int(g('feed_total'))} 个，失败 {failed} 个"
    jobs[1]["detail"] = f"累计 {int(g('scan_total'))} 次"
    jobs[2]["detail"] = f"累计 {int(g('funnel_total'))} 次"
    jobs[3]["detail"] = f"累计 {int(g('run_total'))} 次"
    jobs[4]["detail"] = f"累计 {int(g('push_total'))} 条"

    # /health 的处理函数直接调用：等价于调一次 /health，但避免自环 HTTP 请求
    from ..routers.system import health

    try:
        api_health = health()
    except Exception as e:  # noqa: BLE001
        api_health = {"status": "error", "detail": str(e)[:160], "time": _now()}

    trade_date = None
    try:
        from . import market as market_svc

        trade_date = market_svc.latest_trade_date()
    except Exception:  # noqa: BLE001
        pass

    return {
        "db": pool_stats(),
        "cache": cache_stats(),
        "data_sources": data_sources,
        "schedule": schedule,
        "jobs": jobs,
        "api_health": api_health,
        "system": {
            "python": _platform.python_version(),
            "platform": _platform.platform(),
            "now": _now(),
            "trade_date": _iso(trade_date),
        },
        "meta": {"degraded": degraded, "local": True},
    }


# ---------- 工作台：学员 / 订单（本地演示数据） ----------

#: 本系统没有学员/订单业务，这里给结构完整的演示数据；``meta.seed`` 明确标注。
_STUDENTS = [
    ("STU0001", "张伟", "zhangwei88", "163.com", "年卡", "active", "2026-01-12", "2027-01-11", "2026-10-24 21:10:03"),
    ("STU0002", "李娜", "lina_trading", "qq.com", "季卡", "active", "2026-03-08", "2026-12-07", "2026-10-25 09:22:41"),
    ("STU0003", "王强", "wangqiang007", "gmail.com", "月卡", "active", "2026-09-26", "2026-11-25", "2026-10-25 08:05:17"),
    ("STU0004", "陈静", "chenjing_fx", "126.com", "年卡", "active", "2025-11-30", "2026-11-29", "2026-10-23 22:47:09"),
    ("STU0005", "刘洋", "liuyang_kline", "outlook.com", "试用", "trial", "2026-10-20", "2026-10-27", "2026-10-25 10:31:55"),
    ("STU0006", "赵敏", "zhaomin_invest", "163.com", "季卡", "expired", "2025-12-15", "2026-03-15", "2026-06-30 19:14:26"),
    ("STU0007", "孙浩", "sunhao_quant", "qq.com", "年卡", "active", "2026-02-19", "2027-02-18", "2026-10-24 15:58:02"),
    ("STU0008", "周婷", "zhouting_chart", "gmail.com", "月卡", "active", "2026-10-02", "2026-11-01", "2026-10-25 07:44:33"),
    ("STU0009", "吴磊", "wulei_board", "139.com", "季卡", "expired", "2026-01-05", "2026-04-05", "2026-05-11 12:03:48"),
    ("STU0010", "郑凯", "zhengkai_lhb", "sina.com", "年卡", "active", "2026-04-27", "2027-04-26", "2026-10-24 20:19:12"),
]

_PAYMENTS = [
    ("PB202610250001", "张伟", "年卡", 588.00, "wechat", "paid", "2026-01-12 10:21:33"),
    ("PB202610240002", "李娜", "季卡", 168.00, "alipay", "paid", "2026-03-08 14:02:19"),
    ("PB202610230003", "王强", "月卡", 68.00, "wechat", "paid", "2026-09-26 20:45:07"),
    ("PB202610220004", "陈静", "年卡", 588.00, "alipay", "paid", "2025-11-30 09:12:54"),
    ("PB202610210005", "刘洋", "试用", 0.00, "free", "paid", "2026-10-20 18:33:41"),
    ("PB202610200006", "赵敏", "季卡", 168.00, "wechat", "refunded", "2025-12-15 11:47:26"),
    ("PB202610190007", "孙浩", "年卡", 588.00, "transfer", "paid", "2026-02-19 16:08:12"),
    ("PB202610180008", "周婷", "月卡", 68.00, "alipay", "pending", "2026-10-25 07:40:55"),
    ("PB202610170009", "吴磊", "季卡", 168.00, "wechat", "expired", "2026-01-05 13:26:04"),
    ("PB202610160010", "郑凯", "年卡", 588.00, "alipay", "paid", "2026-04-27 21:57:39"),
]


def _page(items: list, page: int, size: int, extra: dict | None = None) -> dict:
    page, size, offset = _clamp(page, size, max_size=50)
    out = {
        "items": items[offset : offset + size],
        "total": len(items),
        "page": page,
        "size": size,
    }
    out.update(extra or {})
    return out


def students(page: int, size: int) -> dict:
    """学员列表（本地演示数据，结构与真实接口一致）。"""
    items = [
        {
            "id": sid,
            "name": name,
            "email_masked": mask_email(f"{local}@{domain}"),
            "plan": plan,
            "status": status,
            "registered_at": f"{reg}T09:00:00",
            "expires_at": f"{exp}T23:59:59",
            "last_login_at": f"{last}+08:00",
        }
        for sid, name, local, domain, plan, status, reg, exp, last in _STUDENTS
    ]
    return _page(items, page, size, {"meta": {"seed": True, "note": "本地演示数据"}})


def payments(page: int, size: int) -> dict:
    """订单列表（本地演示数据，结构与真实接口一致）。"""
    items = [
        {
            "order_no": order_no,
            "user": user,
            "plan": plan,
            "amount": amount,
            "method": method,
            "status": status,
            "created_at": f"{created}+08:00",
            "paid_at": f"{created}+08:00" if status == "paid" else None,
        }
        for order_no, user, plan, amount, method, status, created in _PAYMENTS
    ]
    return _page(items, page, size, {"meta": {"seed": True, "note": "本地演示数据"}})


# ---------- 工作台：反馈处理（真实读写 app_feedbacks） ----------

_CATEGORY_LABEL = {
    "bug": "缺陷",
    "idea": "建议",
    "question": "咨询",
    "advice": "建议",
    "other": "其他",
}
_ALLOWED_STATUS = ("open", "pending", "replied", "closed")


def feedbacks(page: int, size: int, status: str = "") -> dict:
    """全部用户的反馈（真实表 ``app_feedbacks``，SQL 分页下推）。"""
    page, size, offset = _clamp(page, size, max_size=100)
    where, params = "WHERE 1=1", ()
    if status:
        where, params = "WHERE status = %s", (status,)
    total_row = _safe_query(f"SELECT COUNT(*)::int AS c FROM app_feedbacks {where}", params)
    total = total_row[0]["c"] if total_row else 0
    rows = _safe_query(
        f"""
        SELECT id, user_id, category, content, contact, status, admin_reply, replied_at, created_at
        FROM app_feedbacks
        {where}
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        (*params, size, offset),
    )
    items = []
    for r in rows:
        uid = r.get("user_id")
        items.append(
            {
                "id": _iso(r.get("id")),
                "user": f"用户-{str(uid)[:8]}" if uid else "匿名用户",
                "user_id": _iso(uid),
                "category": r.get("category") or "other",
                "category_label": _CATEGORY_LABEL.get(r.get("category"), "其他"),
                "content": (r.get("content") or "")[:2000],
                "contact": r.get("contact"),
                "status": r.get("status") or "open",
                "admin_reply": r.get("admin_reply"),
                "replied_at": _iso(r.get("replied_at")),
                "created_at": _iso(r.get("created_at")),
            }
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "meta": {"source": "app_feedbacks", "seed": False},
    }


def feedback_update(feedback_id: str, payload: dict) -> dict:
    """回复/变更一条反馈：写 ``status``/``admin_reply``/``replied_at``。

    为什么带 replied_at：状态一变就需要「谁在什么时候处理的」，否则工作台
    没法区分「已回复」和「一直没人管」。
    """
    from ..db import execute, query_one

    payload = payload or {}
    status = payload.get("status")
    reply = payload.get("admin_reply")
    if status is not None and status not in _ALLOWED_STATUS:
        raise ValueError(f"status 必须是 {'/'.join(_ALLOWED_STATUS)} 之一")

    row = query_one(
        "SELECT id, status, admin_reply FROM app_feedbacks WHERE id = %s", (feedback_id,)
    )
    if not row:
        raise KeyError(feedback_id)

    sets, params = [], []
    if status is not None:
        sets.append("status = %s")
        params.append(status)
    if reply is not None:
        sets.append("admin_reply = %s")
        params.append(str(reply)[:4000])
        if not status:  # 只回了内容没指定状态 → 默认置为已回复
            sets.append("status = %s")
            params.append("replied")
        sets.append("replied_at = NOW()")
    if not sets:
        raise ValueError("body 至少要包含 status 或 admin_reply")
    params.append(feedback_id)
    execute(f"UPDATE app_feedbacks SET {', '.join(sets)} WHERE id = %s", tuple(params))

    fresh = query_one(
        "SELECT id, user_id, category, content, contact, status, admin_reply, replied_at, created_at "
        "FROM app_feedbacks WHERE id = %s",
        (feedback_id,),
    )
    uid = fresh.get("user_id")
    return {
        "id": _iso(fresh.get("id")),
        "user": f"用户-{str(uid)[:8]}" if uid else "匿名用户",
        "category": fresh.get("category"),
        "category_label": _CATEGORY_LABEL.get(fresh.get("category"), "其他"),
        "content": (fresh.get("content") or "")[:2000],
        "contact": fresh.get("contact"),
        "status": fresh.get("status"),
        "admin_reply": fresh.get("admin_reply"),
        "replied_at": _iso(fresh.get("replied_at")),
        "created_at": _iso(fresh.get("created_at")),
        "meta": {"updated": True},
    }


# ---------- 个人资料 ----------

DEFAULT_PREFERENCES = {
    "theme": "dark",
    "language": "zh-CN",
    "news_alert_enabled": False,
    "holding_alert_enabled": True,
    "compact_table": False,
    "email_notify": False,
}

_PROFILE_COUNT_SQL = """
SELECT
  (SELECT COUNT(*) FROM app_watchlist_items)::int AS watchlist_count,
  (SELECT COUNT(*) FROM app_holdings)::int AS holding_count,
  (SELECT COUNT(*) FROM app_feedbacks)::int AS feedback_count,
  to_regclass('public.app_orders') IS NOT NULL AS has_orders
"""


def _profile_counts() -> dict:
    """统计数字全部用 SQL 真查（表不存在/为空则为 0），不用写死的假数。"""
    counts = {"watchlist_count": 0, "holding_count": 0, "feedback_count": 0, "order_count": 0}
    rows = _safe_query(_PROFILE_COUNT_SQL)
    if rows:
        r = rows[0]
        counts["watchlist_count"] = int(r.get("watchlist_count") or 0)
        counts["holding_count"] = int(r.get("holding_count") or 0)
        counts["feedback_count"] = int(r.get("feedback_count") or 0)
        if r.get("has_orders"):
            o = _safe_query("SELECT COUNT(*)::int AS c FROM app_orders")
            if o:
                counts["order_count"] = int(o[0]["c"])
    return counts


def profile(request=None) -> dict:
    """个人资料：/auth/me + app_settings 的 profile/preferences + 真实统计。"""
    from ..routers.system import auth_me

    me = auth_me(request)
    prof = get_setting("profile") or {}
    prefs = get_setting("preferences") or prof.get("preferences") or dict(DEFAULT_PREFERENCES)

    row = _auth_user_row()  # 真实 auth.users 行（可能为空 → 回落 profile）
    created_at = (row.get("created_at") if row else None) or prof.get("created_at") or me.get("created_at")
    last_login = (row.get("last_sign_in_at") if row else None) or prof.get("last_login")

    return {
        "username": me.get("username"),
        "nickname": me.get("nickname"),
        "email_masked": mask_email(me.get("email")),
        "avatar": prof.get("avatar") or me.get("avatar"),
        "role": me.get("role"),
        "plan": me.get("plan"),
        "plan_expiry": _iso(me.get("plan_expiry")),
        "membership": me.get("membership"),
        "created_at": _iso(created_at),
        "last_login": _iso(last_login),
        "preferences": prefs,
        "stats": _profile_counts(),
        "meta": {
            "local": True,
            "source": "auth.users" if row else "app_settings:profile",
            "degraded": not bool(row),
        },
    }


# ---------- 管理端全量设置 ----------

#: app_settings 为空时返回的默认骨架（GET 不写库，只给前端一份可用的默认值）。
DEFAULT_SETTINGS = {
    "general": {
        "platform_name": "量化投研一体化平台",
        "announcement": "",
        "online_users": 1,
        "register_enabled": True,
        "maintenance": False,
        "icp": "",
    },
    "ai": {
        "enabled": False,
        "provider": "openai-compatible",
        "base_url": "",
        "model": "",
        "api_key_masked": "",
        "temperature": 0.3,
        "timeout_sec": 60,
        "daily_quota": 200,
    },
    "data_sources": {
        "auto_sync": True,
        "poll_interval_sec": 900,
        "retry_times": 3,
        "providers": ["akshare", "eastmoney", "ths", "local"],
        "realtime_priority": ["sina", "tencent", "akshare"],
    },
    "trading": {
        "commission_rate": 0.0003,
        "min_commission": 5.0,
        "stamp_tax_rate": 0.0005,
        "transfer_fee_rate": 0.00002,
        "lot_size": 100,
        "t_plus_1": True,
    },
}

#: general → site 的字段映射：/settings/public 读的是 ``site`` 这把 key，
#: 不做镜像的话「通用设置」改了却对外不生效（典型的静默失效 bug）。
_GENERAL_TO_SITE = {
    "platform_name": "site_name",
    "announcement": "announcement",
    "online_users": "online_users",
    "register_enabled": "register_enabled",
    "maintenance": "maintenance",
    "icp": "icp",
}


def settings_get() -> dict:
    """管理端全量设置：返回 app_settings 全部键值，缺省段落补默认骨架。"""
    rows = _safe_query("SELECT key, value, updated_at FROM app_settings ORDER BY key")
    stored = {r["key"]: r["value"] for r in rows}
    out = copy.deepcopy(DEFAULT_SETTINGS)

    site = stored.get("site") or {}
    if isinstance(site, dict) and "general" not in stored:
        # general 默认值用 site 的真实值兜底，保证设置页与 /settings/public 同源
        for g_key, s_key in _GENERAL_TO_SITE.items():
            if s_key in site:
                out["general"][g_key] = site[s_key]

    out.update({k: v for k, v in stored.items()})  # 真实存储的键优先
    for k in DEFAULT_SETTINGS:  # 存储里没有的段落补默认骨架
        if k not in out:
            out[k] = copy.deepcopy(DEFAULT_SETTINGS[k])

    updated = max((_iso(r.get("updated_at")) for r in rows if r.get("updated_at")), default=None)
    out["_meta"] = {
        "keys": len(stored),
        "empty": not stored,
        "updated_at": updated,
        "source": "app_settings" if stored else "default",
        "note": "以 _ 开头的键会被 PUT 忽略（前端回传整份 GET 结果时的元信息）",
    }
    return jsonify(out)


def settings_put(payload: dict) -> dict:
    """批量写 ``{key: value}``，逐个 set_setting；返回写入的键。

    ``_`` 开头的键（如 ``_meta``）跳过，避免前端把元信息当设置写回库。
    """
    if not isinstance(payload, dict):
        raise ValueError("body 必须是 {key: value} 字典")

    written, skipped, errors = [], [], []
    for key, value in payload.items():
        key = str(key)
        if key.startswith("_"):
            skipped.append(key)
            continue
        try:
            json.dumps(value, ensure_ascii=False)  # 预校验：set_setting 内部也用 json.dumps
        except (TypeError, ValueError) as exc:
            errors.append({"key": key, "error": f"值无法序列化为 JSON: {exc}"})
            continue
        try:
            set_setting(key, value)
            written.append(key)
        except Exception as exc:  # noqa: BLE001
            errors.append({"key": key, "error": str(exc)[:200]})

        # general → site 镜像：/settings/public 读的是 ``site``，不镜像的话
        # 「通用设置」改了对外完全不生效（静默失效）。
        if key == "general" and isinstance(value, dict) and any(g in value for g in _GENERAL_TO_SITE):
            site = dict(get_setting("site") or {})
            for g_key, s_key in _GENERAL_TO_SITE.items():
                if g_key in value:
                    site[s_key] = value[g_key]
            try:
                set_setting("site", site)
                written.append("site")
            except Exception as exc:  # noqa: BLE001
                errors.append({"key": "site", "error": str(exc)[:200]})

    return {
        "written": written,
        "count": len(written),
        "skipped": skipped,
        "errors": errors,
    }
