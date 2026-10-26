# -*- coding: utf-8 -*-
"""B3. 用户反馈：提交、我的反馈、撤回。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..services import feedback as svc

router = APIRouter(tags=["feedback"])


@router.get("/feedbacks/my")
def my_feedbacks(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100)):
    return svc.list_my(page, size)


@router.post("/feedbacks")
def create_feedback(payload: dict):
    try:
        return svc.create(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.patch("/feedbacks/{feedback_id}")
def patch_feedback(feedback_id: str, payload: dict):
    # 当前只支持撤回；其它状态变更走工作台 /workbench/feedbacks
    status = str((payload or {}).get("status") or "withdrawn")
    if status != "withdrawn":
        raise HTTPException(status_code=400, detail="仅支持撤回（status=withdrawn）")
    item = svc.withdraw(feedback_id)
    if not item:
        raise HTTPException(status_code=404, detail="反馈不存在或已关闭")
    return item


@router.delete("/feedbacks/{feedback_id}")
def delete_feedback(feedback_id: str):
    item = svc.withdraw(feedback_id)
    if not item:
        raise HTTPException(status_code=404, detail="反馈不存在或已关闭")
    return {"ok": True, "id": item["id"], "status": item["status"]}
