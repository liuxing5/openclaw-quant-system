# -*- coding: utf-8 -*-
"""荐股：五策略对比页的结构化数据。"""
from fastapi import APIRouter, HTTPException, Query
from concurrent.futures import ThreadPoolExecutor

from ..services import ai_research, funnel

router = APIRouter(tags=["recommend"])


def _safe_research(code: str):
    try:
        return ai_research.research(code)
    except Exception:  # noqa: BLE001 单个失败不拖垮整批
        return None


@router.get("/recommend/ai-review")
def recommend_ai_review(codes: str = Query(..., description="逗号分隔股票代码")):
    """对当前策略候选做一次批量 AI 多 Agent 复核。"""
    targets = [c.strip() for c in codes.replace("，", ",").split(",") if c.strip()][:12]
    if not targets:
        return {"items": []}
    items = []
    with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
        results = list(pool.map(_safe_research, targets))
    for data in results:
        if data:
            items.append(
                {
                    "code": data["ts_code"],
                    "name": data["name"],
                    "decision": data["decision"],
                    "risk_flags": data.get("risk_flags") or [],
                    "agents": data.get("agents") or [],
                    "skills": [s for s in (data.get("skills") or []) if s.get("matched")],
                }
            )
    return {"items": items}


@router.get("/recommend/funnel")
def recommend_funnel():
    try:
        return funnel.funnel_data()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"荐股数据源暂不可用: {exc}") from None
