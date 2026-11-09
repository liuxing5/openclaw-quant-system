# -*- coding: utf-8 -*-
"""YAML 策略技能：外部 daily_stock_analysis 的 Strategy Skill 思路本地化。

技能文件放在 `strategies/agent_skills/*.yaml`，新增 YAML 即可扩展，无需改代码。
支持自然语言 instructions + 可选声明式 rules；rules 可被本地规则引擎执行。
"""
from __future__ import annotations

import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = ROOT / "strategies" / "agent_skills"


def _f(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def load_skills() -> list[dict]:
    out = []
    if not SKILL_DIR.is_dir():
        return out
    for p in sorted(SKILL_DIR.glob("*.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        name = str(d.get("name") or p.stem).strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "display_name": d.get("display_name") or name,
                "description": d.get("description") or "",
                "category": d.get("category") or "framework",
                "market_regimes": d.get("market_regimes") or [],
                "instructions": d.get("instructions") or "",
                "rules": d.get("rules") or [],
                "signal": d.get("signal") or "buy",
                "source": str(p.relative_to(ROOT)).replace("\\", "/"),
            }
        )
    return out


def _cmp(left, op, right) -> bool:
    if left is None:
        return False
    try:
        if op == ">":
            return float(left) > float(right)
        if op == ">=":
            return float(left) >= float(right)
        if op == "<":
            return float(left) < float(right)
        if op == "<=":
            return float(left) <= float(right)
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "near":
            return bool(right) and abs(float(left) / float(right) - 1) <= float(right if isinstance(right, float) else 0)
        if op == "between":
            return isinstance(right, (list, tuple)) and len(right) == 2 and float(right[0]) <= float(left) <= float(right[1])
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    return False


def _eval_rule(rule: dict, ctx: dict) -> tuple[bool, str]:
    field = rule.get("field")
    op = rule.get("op")
    left = ctx.get(field)
    right = rule.get("field2")
    if isinstance(right, str) and right in ctx:
        right = ctx.get(right)
    else:
        right = rule.get("value")
    if op == "near":
        # YAML 的 value 是允许偏离比例
        try:
            ok = left is not None and right is not None and float(right) > 0 and abs(float(left) / float(right) - 1) <= float(rule.get("value") or 0)
        except (TypeError, ValueError, ZeroDivisionError):
            ok = False
    else:
        ok = _cmp(left, op, right)
    desc = f"{field} {op} {right}"
    return ok, desc


def evaluate(skill: dict, ctx: dict) -> dict:
    rules = skill.get("rules") or []
    checks = [_eval_rule(r, ctx) for r in rules]
    matched = bool(checks) and all(x[0] for x in checks)
    if not rules:
        # 只有自然语言说明的技能不自动判定，留给 LLM/人工
        matched = False
    confidence = 0.72 if matched else 0.25
    reasons = [x[1] for x in checks]
    if not rules:
        reasons = ["仅自然语言策略，未配置自动规则"]
    return {
        "skill": skill["name"],
        "name": skill.get("display_name") or skill["name"],
        "category": skill.get("category"),
        "matched": matched,
        "signal": skill.get("signal") if matched else "hold",
        "confidence": confidence,
        "reason": "；".join(reasons),
        "rules": rules,
        "instructions": skill.get("instructions") or "",
        "source": skill.get("source"),
    }


def evaluate_all(ctx: dict) -> list[dict]:
    return [evaluate(s, ctx) for s in load_skills()]
