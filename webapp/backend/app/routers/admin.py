# -*- coding: utf-8 -*-
"""D3. 系统管理 / 工作台 / 权限 / 审计 / 个人资料 / 管理端全量设置。

路由层只做参数绑定（分页参数、路径参数、body），业务实现全部在
``services/admin.py``；``/settings/public``、``/auth/me``、``/system/data-sources``
已在 ``routers/system.py`` 注册，这里不重复定义。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ..services import admin as svc

router = APIRouter(tags=["admin"])


@router.get("/permissions/menus")
def permission_menus():
    """当前用户可见菜单：本系统单用户本地版，返回全站菜单树（全量放行）。"""
    return svc.jsonify(svc.menus())


@router.get("/system/users")
def system_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=64, description="按用户名/昵称/邮箱模糊过滤"),
):
    """本地用户列表：读 ``auth.users``（空表时回落 /auth/me profile），邮箱脱敏。"""
    return svc.jsonify(svc.local_users(page, size, keyword))


@router.get("/system/permissions")
def system_permissions():
    """角色-权限矩阵：权限项由菜单树推导，保证与 /permissions/menus 对齐。"""
    return svc.jsonify(svc.permission_matrix())


@router.get("/system/audit-logs")
def system_audit_logs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    actor: str = Query("", max_length=64, description="按操作者/动作/IP 过滤"),
):
    """审计日志：优先 ``auth.audit_log_entries``，无数据时降级为合成操作日志。"""
    return svc.jsonify(svc.audit_logs(page, size, actor))


@router.get("/workbench/monitor")
def workbench_monitor():
    """运行监控：连接池、缓存、数据源、调度、任务、/health、系统信息。"""
    return svc.jsonify(svc.monitor())


@router.get("/workbench/students")
def workbench_students(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """学员列表：本系统无此业务，返回结构完整的本地演示数据（meta.seed）。"""
    return svc.jsonify(svc.students(page, size))


@router.get("/workbench/payments")
def workbench_payments(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """订单列表：本系统无此业务，返回结构完整的本地演示数据（meta.seed）。"""
    return svc.jsonify(svc.payments(page, size))


@router.get("/workbench/feedbacks")
def workbench_feedbacks(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str = Query("", max_length=16, description="按状态过滤 open/pending/replied/closed"),
):
    """全部用户的反馈（真实读 ``app_feedbacks``，SQL 分页）。"""
    return svc.jsonify(svc.feedbacks(page, size, status))


@router.patch("/workbench/feedbacks/{feedback_id}")
def workbench_feedback_patch(feedback_id: str, payload: dict):
    """回复/变更一条反馈：写 ``status``/``admin_reply``/``replied_at``。"""
    try:
        return svc.jsonify(svc.feedback_update(feedback_id, payload))
    except KeyError:
        raise HTTPException(status_code=404, detail="反馈不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/profile")
def get_profile(request: Request):
    """个人资料：/auth/me + app_settings profile/preferences + 真实统计计数。"""
    return svc.jsonify(svc.profile(request))


@router.get("/settings/all")
def settings_all():
    """管理端全量设置：返回 app_settings 全部键值（空则给默认骨架）。"""
    return svc.settings_get()  # 内部已 jsonify


@router.put("/settings/all")
def settings_all_put(payload: dict):
    """批量写 ``{key: value}``，逐个落库并返回写入的键（``_`` 开头的键跳过）。"""
    try:
        return svc.jsonify(svc.settings_put(payload))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
