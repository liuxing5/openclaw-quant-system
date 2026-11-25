# -*- coding: utf-8 -*-
"""统一市场情绪口径：TG 推送与 /dashboard 使用同一事实来源。

历史问题（2026-11-25 修复）
--------------------------
旧 ``zuiyou1.fetch_market_sentiment()`` 抓东财 ``f116/f117``（实际是总市值/
流通市值）当涨跌家数，数值越界后回落 hardcode ``1500/1500``；连板高度误用
涨跌幅字段 ``zdf`` 且要求 int，回落默认值 2；炸板判定在涨停池内恒为 0。
结果是 TG 显示「涨停 45 / 跌停 0 / 涨跌比 1.00 / 封板率 100% / 91.9 高潮」，
而同一天 /dashboard 的真实数据是「涨停 58 / 跌停 49 / 上涨 567 / 下跌 4588 /
封板率 59.8% / 38.3 低迷」。

统一方案
--------
唯一事实来源为 webapp 的 ``live_market``（腾讯全市场实时快照 + daily_quotes）：

1. 优先请求线上 ``/api/v1/dashboard/overview``（与页面同一进程、同一缓存）；
2. 线上不可用时本地 import ``webapp.backend.app.services.live_market`` 直接计算
   （同一份代码，需 DATABASE_URL / WEBAPP_DATABASE_URL）；
3. 两者都失败才由调用方返回保守 fallback，并在情绪标签标注「接口异常」。

本模块刻意不在模块级 import requests / psycopg2，便于纯逻辑单元测试。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

DEFAULT_OVERVIEW_URL = (
    "https://quant-research-platform-8kb8.onrender.com/api/v1/dashboard/overview"
)

# /dashboard 的六维等权分档 -> 八步法策略使用的语义标签。
# 只改标签，不改分数：策略与页面显示的综合分始终是同一个数字。
LEVEL_TO_MOOD = {
    "亢奋": "高潮",
    "活跃": "火热",
    "温和": "正常",
    "低迷": "冷淡",
    "冰点": "极冷",
}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_overview(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把 /dashboard/overview 的响应归一化为推送所需的指标字典。

    所有计数都来自快照，不再使用任何默认值填充涨跌家数；缺字段会直接报错，
    由调用方走 fallback，避免再出现「假数据算出高分」。
    """
    if not isinstance(raw, dict):
        raise ValueError("overview 响应不是 dict")
    breadth = raw.get("breadth") or {}
    sentiment = raw.get("sentiment") or {}
    if not breadth or not sentiment:
        raise ValueError("overview 缺少 breadth / sentiment 字段")

    up = _as_int(breadth.get("up"), -1)
    down = _as_int(breadth.get("down"), -1)
    limit_up = _as_int(breadth.get("limit_up"), -1)
    limit_down = _as_int(breadth.get("limit_down"), -1)
    if min(up, down, limit_up, limit_down) < 0:
        raise ValueError("breadth 缺少 up/down/limit_up/limit_down 真实计数")
    total = _as_int(breadth.get("total"), up + down + _as_int(breadth.get("flat")))

    touched = _as_int(breadth.get("touched"), limit_up)
    broken = _as_int(breadth.get("broken"), max(0, touched - limit_up))
    if touched < limit_up:
        touched = limit_up + broken
    seal_rate = (limit_up / touched) if touched else 0.0
    explode_rate = (broken / touched) if touched else 0.0

    score = _as_float(sentiment.get("score"))
    if score is None:
        values = [
            _as_float(d.get("value"))
            for d in (sentiment.get("dims") or [])
            if isinstance(d, dict) and _as_float(d.get("value")) is not None
        ]
        score = sum(values) / len(values) if values else 50.0

    level = str(sentiment.get("level") or "低迷")
    mood = LEVEL_TO_MOOD.get(level, "正常")

    ladder = raw.get("ladder") or {}
    tiers = ladder.get("tiers") or []
    max_streak = max([_as_int(t.get("streak")) for t in tiers if isinstance(t, dict)] or [0])
    if not max_streak:
        for dim in sentiment.get("dims") or []:
            if not isinstance(dim, dict) or dim.get("key") != "streak":
                continue
            match = re.search(r"最高\s*(\d+)\s*板", str(dim.get("hint") or ""))
            if match:
                max_streak = int(match.group(1))
                break

    return {
        "score": round(float(score), 1),
        "level": level,
        "mood": mood,
        "up": up,
        "down": down,
        "flat": _as_int(breadth.get("flat")),
        "total": total,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "touched": touched,
        "broken": broken,
        "seal_rate": seal_rate,
        "explode_rate": explode_rate,
        "up_down_ratio": (up / down) if down else float(up),
        "max_streak": max_streak,
        "trade_date": sentiment.get("trade_date") or breadth.get("trade_date") or raw.get("trade_date"),
        "as_of": sentiment.get("as_of") or breadth.get("as_of") or raw.get("as_of"),
        "source": raw.get("data_source") or "tencent-live + daily_quotes",
    }


def describe(s: Dict[str, Any]) -> str:
    """生成与旧 TG 模板兼容、但数据真实的情绪文本。"""
    return "\n".join([
        "  📊 多维情绪指标（口径：与 /dashboard 一致）",
        f"    涨停家数: {s['limit_up']} | 跌停家数: {s['limit_down']}",
        f"    上涨家数: {s['up']} | 下跌家数: {s['down']} | 涨跌比: {s['up_down_ratio']:.2f}",
        f"    封板率: {s['seal_rate']:.1%} | 炸板率: {s['explode_rate']:.1%}",
        f"    最高连板: {s['max_streak']}板",
        f"    综合情绪分: {s['score']:.1f} | 情绪状态: {s['mood']}（大盘口径: {s['level']}）",
        f"    数据日期: {s['trade_date']} | 快照时间: {s['as_of']} | 来源: {s['source']}",
    ])


def fetch_from_api(url: Optional[str] = None, timeout: float = 45.0) -> Dict[str, Any]:
    """优先路径：直接读取线上 /dashboard，保证页面与推送数值完全相同。"""
    import requests  # 延迟导入，保持本模块可被纯逻辑测试

    target = url or os.getenv("DASHBOARD_OVERVIEW_URL") or DEFAULT_OVERVIEW_URL
    response = requests.get(target, timeout=(5, timeout))
    response.raise_for_status()
    raw = response.json()
    payload = normalize_overview(raw)
    payload["source"] = raw.get("data_source") or "Render /dashboard"
    return payload


def fetch_from_local() -> Dict[str, Any]:
    """降级路径：本地复用 webapp 同一套 live_market 计算逻辑。"""
    from webapp.backend.app.services import live_market  # 延迟导入

    breadth = live_market.breadth()
    sentiment = live_market.sentiment()
    ladder = live_market.limit_ladder(detail=False)
    if not breadth or not sentiment:
        raise ValueError("本地 live_market 快照不可用")
    raw = {
        "breadth": breadth,
        "sentiment": sentiment,
        "ladder": ladder,
        "as_of": breadth.get("as_of"),
        "trade_date": breadth.get("trade_date"),
        "data_source": "tencent-live + daily_quotes（本地）",
    }
    return normalize_overview(raw)


def fetch() -> Dict[str, Any]:
    """线上优先、本地兜底；全部失败时抛错，由调用方决定保守分数。"""
    errors: List[str] = []
    try:
        return fetch_from_api()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"线上 /dashboard: {exc}")
    try:
        return fetch_from_local()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"本地 live_market: {exc}")
    raise RuntimeError("; ".join(errors))
