# -*- coding: utf-8 -*-
"""会员账号接口：注册/登录/开通。"""
from fastapi import APIRouter, HTTPException

from ..services import auth as svc

router = APIRouter(tags=["auth"])


@router.post("/auth/register")
def register(payload: dict):
    try:
        return svc.register(
            (payload or {}).get("email"),
            (payload or {}).get("password"),
            (payload or {}).get("nickname"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/auth/login")
def login(payload: dict):
    try:
        return svc.login((payload or {}).get("email"), (payload or {}).get("password"))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None


@router.post("/auth/activate")
def activate(payload: dict):
    try:
        return svc.activate(
            (payload or {}).get("email"),
            (payload or {}).get("plan"),
            (payload or {}).get("admin_key"),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
