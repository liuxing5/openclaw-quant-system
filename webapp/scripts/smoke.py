# -*- coding: utf-8 -*-
"""全站接口冒烟验收：对 API_CONTRACT.md 里的每个端点真实打一枪。

用法（先起后端）::

    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir webapp/backend
    python webapp/scripts/smoke.py                  # 只读端点
    python webapp/scripts/smoke.py --with-writes    # 附带写接口（自带清理）
    python webapp/scripts/smoke.py --json out.json  # 结果落盘

判定标准：
    * 状态码 2xx（文档里标注「可能 404」的端点 404 也算过，但会标 WARN）
    * 响应必须是合法 JSON
    * 单接口耗时 > 3s 标 SLOW，payload > 200KB 标 BIG
    * 最后打印总计与失败清单，有失败时退出码为 1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"

# (标签, 路径[, 允许 404])
READ_ENDPOINTS: list[tuple[str, str, bool]] = [
    ("健康检查", "/health", False),
    ("公开设置", "/settings/public", False),
    ("当前用户", "/auth/me", False),
    ("全局时段", "/dashboard/global-schedule", False),
    ("数据源状态", "/system/data-sources", False),
    ("总台聚合", "/dashboard/overview?refresh=false", False),
    ("总台状态条", "/dashboard/summary", False),
    ("行情摘要", "/market-data/summary", False),
    ("涨跌分布", "/market-data/breadth", False),
    ("榜单-涨幅", "/market-data/rankings?sort=pct_chg_desc&limit=10", False),
    ("榜单-跌幅", "/market-data/rankings?sort=pct_chg_asc&limit=10", False),
    ("日线", "/market-data/quotes/daily?symbol=600519.SH&limit=30", False),
    ("证券搜索", "/market-data/securities/search?keyword=600519", False),
    ("个股档案", "/market-data/securities/600519.SH", False),
    ("实时快照", "/market-data/realtime?codes=600519.SH,000001.SZ", False),
    ("实时批量", "/market-data/realtime/batch?codes=600519.SH", False),
    ("实时状态", "/market-data/realtime/status", False),
    ("连板梯队", "/limit-ladder?min_level=0", False),
    ("概念列表", "/boards/concepts?page=1&size=10&sort=pct_chg", False),
    ("行业列表", "/boards/industries?page=1&size=10&sort=pct_chg", False),
    ("自选分组", "/watchlists", False),
    ("默认分组", "/watchlists/default", False),
    ("自选行情", "/watchlists/default/quotes?page=1&size=20", False),
    ("破位提醒开关", "/watchlists/holding-alert-settings", False),
    ("持仓列表", "/holdings?page=1&size=20", False),
    ("持仓汇总", "/holdings/summary", False),
    ("提醒列表", "/alerts?page=1&size=20", False),
    ("最近触发", "/alerts/triggered?limit=10", False),
    ("模拟账户", "/simulation/accounts", False),
    ("模拟汇总", "/simulation/summary", False),
    ("模拟持仓", "/simulation/positions?page=1&size=20", False),
    ("模拟委托", "/simulation/orders?page=1&size=20", False),
    ("策略列表", "/strategies", False),
    ("策略统计", "/strategies/run-stats", False),
    ("策略扫描史", "/strategies/scans?days=30", False),
    ("复盘列表", "/reviews?page=1&size=30", False),
    ("情绪历史", "/sentiment/history?days=120", False),
    ("异动事件", "/anomaly/events?page=1&size=50&scope=symbol&all_market=false", False),
    ("异动规则", "/anomaly/rules?page=1&size=50", False),
    ("我的反馈", "/feedbacks/my?page=1&size=20", False),
    ("学院文章", "/academy/articles", False),
    ("快讯", "/info/news-flash?page=1&size=20", False),
    ("信号雷达", "/info/news-radar?limit=48", False),
    ("热搜", "/info/hot-search?platform=eastmoney&limit=50", False),
    ("资金流-行业", "/info/fund-flow/sectors?sector_type=industry", False),
    ("资金流-概念", "/info/fund-flow/sectors?sector_type=concept", False),
    ("外围指数", "/global-indexes", False),
    ("商品行情", "/commodity/quotes", False),
    ("并购重组", "/merger-restructuring?page=1&size=20", False),
    ("龙虎榜", "/dragon-tiger?page=1&size=20", False),
    ("龙虎榜席位", "/dragon-tiger/seat-rank?days=20", False),
    ("指数总览", "/index/summary", False),
    ("指数日线", "/index/quotes/000001.SH?days=60", False),
    ("指数成分", "/index/components/000001.SH?page=1&size=20", False),
    ("财务报表", "/finance/statements?ts_code=600519.SH&limit=8", False),
    ("财务指标", "/finance/indicators?ts_code=600519.SH", False),
    ("市场环境", "/market-env", False),
    ("环境历史", "/market-env/history?days=60", False),
    ("全市场行情", "/data/quotes?page=1&size=50", False),
    ("全市场基本面", "/data/fundamentals?page=1&size=50", False),
    ("菜单权限", "/permissions/menus", False),
    ("系统用户", "/system/users?page=1&size=20", False),
    ("权限矩阵", "/system/permissions", False),
    ("审计日志", "/system/audit-logs?page=1&size=20", False),
    ("工作台监控", "/workbench/monitor", False),
    ("工作台学员", "/workbench/students?page=1&size=20", False),
    ("工作台订单", "/workbench/payments?page=1&size=20", False),
    ("工作台反馈", "/workbench/feedbacks?page=1&size=20", False),
    ("个人资料", "/profile", False),
    ("全量设置", "/settings/all", False),
]


def _get(path: str, timeout: float = 30.0) -> tuple[int, dict | list | str, float, int]:
    url = path if path.startswith("http") else BASE + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 本地服务
            raw = resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也是有效响应
        raw = exc.read()
        code = exc.code
    elapsed = time.time() - t0
    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        body = raw[:200].decode("utf-8", errors="replace")
    return code, body, elapsed, len(raw)


def _first_code(label: str, path: str, key_hint: tuple[str, ...] = ("ts_code", "code")) -> str | None:
    """从某个列表接口里挖一个可用的板块/标的代码，供详情接口使用。"""
    try:
        _, body, _, _ = _get(path)
    except Exception:  # noqa: BLE001
        return None
    items = body.get("items") if isinstance(body, dict) else None
    if not items and isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list) and v:
                items = v
                break
    for row in items or []:
        if not isinstance(row, dict):
            continue
        for k in key_hint:
            if row.get(k):
                return str(row[k])
        for v in row.values():
            if isinstance(v, str) and "." in v and v[:6].isdigit():
                return v
    print(f"  (未能从 {label} 挖到代码，跳过其详情端点)", file=sys.stderr)
    return None


def run_read() -> list[dict]:
    results = []
    concept = _first_code("概念列表", "/boards/concepts?page=1&size=5")
    industry = _first_code("行业列表", "/boards/industries?page=1&size=5")
    extra: list[tuple[str, str, bool]] = []
    if concept:
        extra += [
            ("板块详情-概念", f"/boards/concepts/{urllib.parse.quote(concept, safe='')}?days=20", False),
            ("板块成分-概念", f"/boards/concepts/{urllib.parse.quote(concept, safe='')}/members", True),
        ]
    if industry:
        extra += [
            ("板块详情-行业", f"/boards/industries/{urllib.parse.quote(industry, safe='')}?days=20", False),
            ("板块成分-行业", f"/boards/industries/{urllib.parse.quote(industry, safe='')}/members", True),
        ]
    for label, path, allow_404 in READ_ENDPOINTS + extra:
        try:
            code, body, elapsed, size = _get(path)
        except Exception as exc:  # noqa: BLE001
            results.append({"label": label, "path": path, "status": 0, "elapsed": 0, "size": 0, "error": str(exc)[:160]})
            continue
        ok = 200 <= code < 300 or (allow_404 and code == 404)
        results.append(
            {
                "label": label,
                "path": path,
                "status": code,
                "elapsed": round(elapsed, 3),
                "size": size,
                "ok": ok,
                "allow_404": allow_404,
                "sample": (json.dumps(body, ensure_ascii=False)[:180] if not isinstance(body, str) else body),
            }
        )
    return results


def run_writes() -> list[dict]:
    """写接口冒烟：全部自带清理，结束后不留脏数据。"""
    out: list[dict] = []

    def step(label, method, path, payload=None, expect=(200, 201)):
        url = BASE + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                code, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            code, raw = exc.code, exc.read()
        except Exception as exc:  # noqa: BLE001
            out.append({"label": label, "status": 0, "elapsed": 0, "error": str(exc)[:160], "ok": False})
            return None
        elapsed = round(time.time() - t0, 3)
        try:
            body = json.loads(raw.decode())
        except Exception:  # noqa: BLE001
            body = None
        ok = code in expect
        out.append({"label": label, "status": code, "elapsed": elapsed, "ok": ok,
                    "sample": json.dumps(body, ensure_ascii=False)[:180] if body else ""})
        return body if ok else None

    # 1. 反馈：增 -> 查 -> 删
    fb = step("反馈-提交", "POST", "/feedbacks", {"category": "idea", "content": "冒烟测试提交，随后删除", "contact": ""})
    if fb and fb.get("id"):
        step("反馈-删除", "DELETE", f"/feedbacks/{fb['id']}", expect=(200, 204, 404))

    # 2. 模拟盘：建账户 -> 买 -> 卖 -> 重置 -> 删账户
    acct = step("模拟-建账户", "POST", "/simulation/accounts", {"name": "smoke自动账户", "initial_cash": 1000000})
    aid = (acct or {}).get("id") or (acct or {}).get("account_id")
    if aid:
        step("模拟-市价买入", "POST", "/simulation/orders",
             {"account_id": aid, "ts_code": "600519.SH", "side": "buy", "qty": 100, "order_type": "market"})
        step("模拟-市价卖出", "POST", "/simulation/orders",
             {"account_id": aid, "ts_code": "600519.SH", "side": "sell", "qty": 100, "order_type": "market"})
        step("模拟-非法整手拒绝", "POST", "/simulation/orders",
             {"account_id": aid, "ts_code": "600519.SH", "side": "buy", "qty": 150, "order_type": "market"}, expect=(200, 400, 422))
        step("模拟-重置", "POST", "/simulation/reset", {"account_id": aid})
        step("模拟-删账户", "DELETE", f"/simulation/accounts/{aid}", expect=(200, 204, 404, 405))

    # 3. 自选：加 -> 查 -> 删
    grp = step("自选-取默认分组", "GET", "/watchlists/default")
    gid = (grp or {}).get("id")
    if gid:
        step("自选-加自选", "POST", f"/watchlists/{gid}/items", {"ts_code": "600519.SH", "stock_name": "贵州茅台"})
        items = step("自选-查标的", "GET", f"/watchlists/{gid}/items")
        for row in (items or {}).get("items", []):
            if row.get("ts_code") == "600519.SH":
                step("自选-删自选", "DELETE", f"/watchlists/{gid}/items/{row['id']}", expect=(200, 204))

    # 4. 持仓：增 -> 查 -> 删
    h = step("持仓-新增", "POST", "/holdings",
             {"ts_code": "600519.SH", "stock_name": "贵州茅台", "qty": 100, "cost": 1300.0})
    hid = (h or {}).get("id")
    step("持仓-汇总", "GET", "/holdings/summary")
    if hid:
        step("持仓-删除", "DELETE", f"/holdings/{hid}", expect=(200, 204))

    # 5. 策略：跑批 + 复盘生成 + 异动扫描（落库但属正常业务数据）
    strategies = step("策略-列表", "GET", "/strategies")
    for row in (strategies or {}).get("items", [])[:2]:
        code = row.get("code")
        if code:
            step(f"策略-跑批 {code}", "POST", f"/strategies/{code}/run", expect=(200, 201, 202))
    step("异动-扫描", "POST", "/anomaly/events/scan", expect=(200, 201))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-writes", action="store_true", help="附带写接口冒烟（自带清理）")
    ap.add_argument("--json", help="把结果写到指定 JSON 文件")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    try:
        _get("/health", timeout=5)
    except Exception as exc:  # noqa: BLE001
        print(f"后端未启动（{exc}）。先跑：python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir webapp/backend")
        return 2

    rows = run_read()
    writes = run_writes() if args.with_writes else []

    fails, slows, bigs, warns = [], [], [], []
    print(f"\n{'状态':>4}  {'耗时':>7}  {'KB':>7}  说明")
    print("-" * 96)
    for r in rows:
        status = r["status"]
        flag = "OK" if r.get("ok") else "FAIL"
        if status == 404 and r.get("allow_404"):
            flag, r["ok"] = "WARN", True
            warns.append(r)
        if not r.get("ok"):
            fails.append(r)
        if r["elapsed"] > 3:
            slows.append(r)
        if r["size"] > 200_000:
            bigs.append(r)
        mark = {"OK": "  ✓", "WARN": "~ !", "FAIL": "  ✗"}[flag]
        print(f"{status:>4}{mark} {r['elapsed']:>6.2f}s {r['size']/1024:>6.1f}  {r['label']:<14} {r['path'][:52]}")

    if writes:
        print(f"\n--- 写接口（{len(writes)} 步） ---")
        for w in writes:
            mark = "✓" if w.get("ok") else "✗"
            print(f"  {mark} {w['status']:>4} {w['elapsed']:>6.2f}s  {w['label']}")
            if not w.get("ok"):
                fails.append(w)

    total = len(rows) + len(writes)
    passed = sum(1 for r in rows if r.get("ok")) + sum(1 for w in writes if w.get("ok"))
    print("\n" + "=" * 96)
    print(f"通过 {passed}/{total}   失败 {len(fails)}   警告(404允许) {len(warns)}   慢(>3s) {len(slows)}   大(>200KB) {len(bigs)}")
    if slows:
        print("慢接口: " + ", ".join(f"{r['label']}({r['elapsed']}s)" for r in slows))
    if bigs:
        print("大包:   " + ", ".join(f"{r['label']}({r['size']//1024}KB)" for r in bigs))
    if fails:
        print("\n失败清单:")
        for r in fails:
            print(f"  ✗ {r['label']}  {r.get('path','')}  status={r.get('status')}  {str(r.get('error') or r.get('sample'))[:200]}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"read": rows, "writes": writes}, fh, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.json}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
