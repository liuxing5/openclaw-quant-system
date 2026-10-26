# -*- coding: utf-8 -*-
"""D1. 资讯 / 内容 / 外围行情读接口。

路由只做参数校验与「降级兜底」的最后把关，SQL 与外部源逻辑都在
``services/content.py``：这样「哪些接口可能失败、失败时返回什么」集中在一处。

降级承诺：任何异常都返回**结构完整**的空数据 + ``meta.degraded=true``，
不把 500 留给前端（外部源更是如此）；异常同时打到 stderr 便于排查。
"""
import sys
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..services import content as svc

router = APIRouter(tags=["content"])


def _guard(fn, empty_factory, label: str) -> dict:
    """统一兜底：服务层抛异常时返回结构完整的空数据，绝不外抛 500。"""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[content] {label} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        payload = empty_factory()
        payload["meta"] = {
            "degraded": True,
            "reason": f"{type(exc).__name__}: {str(exc)[:180]}",
        }
        return payload


# ---------------------------------------------------------------------------
# 学院（原创静态内容，零 IO）
# ---------------------------------------------------------------------------


@router.get("/academy/articles")
def academy_articles(category: Optional[str] = Query(None, description="盘面基础/技术与量价/阅读经典")):
    return svc.academy_articles(category)


@router.get("/academy/articles/{article_id}")
def academy_article(article_id: str):
    item = svc.academy_article(article_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"文章不存在: {article_id}")
    return item


# ---------------------------------------------------------------------------
# 快讯 / 雷达 / 热搜 / 板块资金流
# ---------------------------------------------------------------------------


@router.get("/info/news-flash")
def info_news_flash(
    source: str = Query("", description="来源别名：cls(财联社)/eastmoney/ths/akshare，空=全部"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    return _guard(
        lambda: svc.news_flash(source=source, page=page, size=size),
        lambda: {"items": [], "total": 0, "page": page, "size": size, "as_of": None},
        "news-flash",
    )


@router.get("/info/news-radar")
def info_news_radar(
    limit: int = Query(48, ge=1, le=168, description="近 N 小时（锚点=最新一条信号）"),
    hours: Optional[int] = Query(None, ge=1, le=168, description="与 limit 等价的别名"),
):
    n = hours or limit
    return _guard(
        lambda: svc.news_radar(hours=n),
        lambda: {"items": [], "total": 0, "hours": n, "as_of": None},
        "news-radar",
    )


@router.get("/info/hot-search")
def info_hot_search(
    platform: str = Query("eastmoney", description="平台参数保留，当前榜单为本地推荐池聚合"),
    limit: int = Query(50, ge=1, le=100),
):
    return _guard(
        lambda: svc.hot_search(platform=platform, limit=limit),
        lambda: {"items": [], "total": 0, "platform": platform, "as_of": None},
        "hot-search",
    )


@router.get("/info/fund-flow/sectors")
def info_fund_flow_sectors(
    sector_type: str = Query("industry", pattern="^(industry|concept)$"),
    limit: int = Query(50, ge=1, le=200),
):
    return _guard(
        lambda: svc.sector_fund_flow(sector_type=sector_type, limit=limit),
        lambda: {
            "items": [],
            "total": 0,
            "trade_date": None,
            "sector_type": sector_type,
        },
        "fund-flow",
    )


# ---------------------------------------------------------------------------
# 外围指数 / 大宗商品（外部源，600s 缓存 + 静态兜底）
# ---------------------------------------------------------------------------


@router.get("/global-indexes")
def global_indexes():
    return _guard(
        svc.global_indexes,
        lambda: {"items": [], "as_of": None},
        "global-indexes",
    )


@router.get("/commodity/quotes")
def commodity_quotes():
    return _guard(
        svc.commodity_quotes,
        lambda: {"items": [], "as_of": None},
        "commodity-quotes",
    )


# ---------------------------------------------------------------------------
# 并购重组 / 龙虎榜
# ---------------------------------------------------------------------------


@router.get("/merger-restructuring")
def merger_restructuring(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100)):
    return _guard(
        lambda: svc.merger_restructuring(page=page, size=size),
        lambda: {"items": [], "total": 0, "page": page, "size": size, "as_of": None},
        "merger-restructuring",
    )


@router.get("/dragon-tiger")
def dragon_tiger(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    date: Optional[str] = Query(None, description="交易日 YYYY-MM-DD"),
    keyword: str = Query("", description="匹配 stock_name 或 reason"),
):
    return _guard(
        lambda: svc.dragon_tiger(page=page, size=size, trade_date=date, keyword=keyword),
        lambda: {"items": [], "total": 0, "page": page, "size": size, "as_of": None},
        "dragon-tiger",
    )


@router.get("/dragon-tiger/seat-rank")
def dragon_tiger_seat_rank(
    days: int = Query(20, ge=1, le=120),
    limit: int = Query(50, ge=1, le=200),
):
    return _guard(
        lambda: svc.dragon_tiger_seat_rank(days=days, limit=limit),
        lambda: {"items": [], "by_seat_type": [], "total": 0, "days": days, "as_of": None},
        "dragon-tiger-seat-rank",
    )
