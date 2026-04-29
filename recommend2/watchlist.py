"""
观察池追踪器 — 监控"接近触发"的票的演变
================================================
主策略每周扫一次全市场,得分 3+ 的票(即使没触发)自动纳入观察池。
下一轮扫描时对比,看哪些票的得分在上升,接近触发。

用法:
  python watchlist.py save     # 把今天 scan 结果里得分 3+ 的票存为观察池
  python watchlist.py update   # 只扫观察池里的票,看谁得分变化
  python watchlist.py diff     # 对比两次快照,突出得分上升的票
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from config import ScreenerConfig, DragonConfig, TZ
from data_loader import DataLoader, to_bs_code
from screener import scan_universe
from dragon_screener import scan_universe_dragon, precompute_sector_sync

WATCHLIST_DIR = Path("./results/watchlist")
WATCHLIST_DIR.mkdir(parents=True, exist_ok=True)


def cmd_save(args):
    """从最新 scan 结果里提取得分 >= min_score 的票存观察池"""
    latest_scan = sorted(Path("./results").glob("scan_*.csv"))
    if not latest_scan:
        print("未找到 scan 结果，先跑 python main.py scan")
        return
    scan_file = latest_scan[-1]
    print(f"从 {scan_file} 提取观察池")
    # 读取时指定 symbol 列为字符串，避免前导零丢失
    df = pd.read_csv(scan_file, dtype={"symbol": str})
    watch = df[df["score"] >= args.min_score].copy()
    today = datetime.now(TZ).strftime("%Y%m%d")
    # 保存时确保 symbol 列是字符串格式
    watch["symbol"] = watch["symbol"].astype(str)
    out = WATCHLIST_DIR / f"watchlist_{today}.csv"
    watch.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"✓ 观察池已存 -> {out}")
    print(f"  共 {len(watch)} 只，得分分布:")
    print(watch["score"].value_counts().sort_index().to_string())


def cmd_update(args):
    """只扫描观察池里的票，生成今天的快照"""
    snapshots = sorted(WATCHLIST_DIR.glob("watchlist_*.csv"))
    if not snapshots:
        print("观察池为空，先跑 python watchlist.py save")
        return

    base_file = snapshots[-1]
    # 读取时指定 symbol 列为字符串，避免前导零丢失
    base = pd.read_csv(base_file, dtype={"symbol": str})
    symbols = list(zip(base["symbol"].astype(str), base["name"]))
    print(f"基线：{base_file.name} ({len(symbols)} 只)")

    loader = DataLoader()
    cfg = ScreenerConfig()
    result = scan_universe(symbols, cfg=cfg, loader=loader)

    today = datetime.now(TZ).strftime("%Y%m%d")
    out = WATCHLIST_DIR / f"snapshot_{today}.csv"
    result.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n✓ 今日快照已存 -> {out}")

    triggered = result[result["triggered"] == True]
    if not triggered.empty:
        print(f"\n🎯 观察池中 {len(triggered)} 只已触发!")
        cols = [c for c in ["symbol", "name", "score", "last_close",
                             "suggested_stop"] if c in triggered.columns]
        print(triggered[cols].to_string(index=False))


def cmd_diff(args):
    """对比最近两次快照，找出得分上升的票"""
    snapshots = sorted(WATCHLIST_DIR.glob("snapshot_*.csv"))
    if len(snapshots) < 2:
        print("至少需要 2 次快照才能对比，先跑几次 python watchlist.py update")
        return

    newer = pd.read_csv(snapshots[-1]).set_index("symbol")
    older = pd.read_csv(snapshots[-2]).set_index("symbol")
    print(f"对比：{snapshots[-2].name} -> {snapshots[-1].name}")

    common = newer.index.intersection(older.index)
    delta = pd.DataFrame({
        "name": newer.loc[common, "name"],
        "score_old": older.loc[common, "score"],
        "score_new": newer.loc[common, "score"],
        "delta": newer.loc[common, "score"] - older.loc[common, "score"],
        "last_close_new": newer.loc[common, "last_close"] if "last_close" in newer.columns else 0,
        "last_fail_new": newer.loc[common, "last_fail"] if "last_fail" in newer.columns else "",
    }).sort_values("delta", ascending=False)

    # 得分上升的 (冲刺触发中)
    rising = delta[delta["delta"] > 0]
    print(f"\n🔺 得分上升 {len(rising)} 只:")
    if not rising.empty:
        print(rising.to_string())

    # 得分持平但已是高分的
    high_stable = delta[(delta["delta"] == 0) & (delta["score_new"] >= 4)]
    if not high_stable.empty:
        print(f"\n⚡ 保持 4+ 分 {len(high_stable)} 只:")
        print(high_stable.to_string())


def cmd_save_dragon(args):
    """从最新 dragon-scan 结果里提取得分 >= min_score 的票存观察池"""
    latest_scan = sorted(Path("./results").glob("dragon_scan_*.csv"))
    if not latest_scan:
        print("未找到 dragon-scan 结果，先跑 python main.py dragon-scan")
        return
    scan_file = latest_scan[-1]
    print(f"从 {scan_file} 提取龙头观察池")
    # 读取时指定 symbol 列为字符串，避免前导零丢失
    df = pd.read_csv(scan_file, dtype={"symbol": str})
    watch = df[df["score"] >= args.min_score].copy()
    today = datetime.now(TZ).strftime("%Y%m%d")
    # 保存时确保 symbol 列是字符串格式（不科学计数法）
    watch["symbol"] = watch["symbol"].astype(str)
    out = WATCHLIST_DIR / f"dragon_watchlist_{today}.csv"
    watch.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"✓ 龙头观察池已存 -> {out}")
    print(f"  共 {len(watch)} 只，得分分布:")
    print(watch["score"].value_counts().sort_index().to_string())


def cmd_update_dragon(args):
    """只扫描龙头观察池里的票，生成今天的快照（极速版）"""
    snapshots = sorted(WATCHLIST_DIR.glob("dragon_watchlist_*.csv"))
    if not snapshots:
        print("龙头观察池为空，先跑 python watchlist.py save-dragon")
        return

    base_file = snapshots[-1]
    # 读取时指定 symbol 列为字符串，避免前导零丢失
    base = pd.read_csv(base_file, dtype={"symbol": str})
    symbols = list(zip(base["symbol"].astype(str), base["name"]))
    print(f"基线：{base_file.name} ({len(symbols)} 只)")

    loader = DataLoader()
    cfg = DragonConfig()
    
    # 优化：观察池只有几只股票，不需要预计算板块同步度
    # 直接使用外部注入的空字典（L4 层会跳过但不影响其他层）
    # 这样可以节省 1-2 分钟的预计算时间
    print("极速模式：跳过板块同步度预计算（观察池扫描）")
    
    # 正式扫描（不注入板块同步度，L4 层自动跳过）
    result = scan_universe_dragon(
        symbols, cfg=cfg, loader=loader,
        sector_sync={},  # 空字典，跳过 L4 层
        eval_date=datetime.now(TZ).strftime("%Y-%m-%d")
    )

    today = datetime.now(TZ).strftime("%Y%m%d")
    out = WATCHLIST_DIR / f"dragon_snapshot_{today}.csv"
    result.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n✓ 龙头今日快照已存 -> {out}")

    triggered = result[result["triggered"] == True]
    if not triggered.empty:
        print(f"\n🎯 龙头观察池中 {len(triggered)} 只已触发!")
        cols = [c for c in ["symbol", "name", "score", "last_close",
                             "break_board_date", "suggested_entry"]
                if c in triggered.columns]
        print(triggered[cols].to_string(index=False))


def cmd_diff_dragon(args):
    """对比最近两次龙头快照，找出得分上升的票"""
    snapshots = sorted(WATCHLIST_DIR.glob("dragon_snapshot_*.csv"))
    if len(snapshots) < 2:
        print("至少需要 2 次龙头快照才能对比，先多跑几次 python watchlist.py update-dragon")
        return

    newer = pd.read_csv(snapshots[-1]).set_index("symbol")
    older = pd.read_csv(snapshots[-2]).set_index("symbol")
    print(f"对比：{snapshots[-2].name} -> {snapshots[-1].name}")

    common = newer.index.intersection(older.index)
    delta = pd.DataFrame({
        "name": newer.loc[common, "name"],
        "score_old": older.loc[common, "score"],
        "score_new": newer.loc[common, "score"],
        "delta": newer.loc[common, "score"] - older.loc[common, "score"],
        "last_close_new": newer.loc[common, "last_close"] if "last_close" in newer.columns else 0,
        "break_board_date_new": newer.loc[common, "break_board_date"] if "break_board_date" in newer.columns else "",
    }).sort_values("delta", ascending=False)

    # 得分上升的
    rising = delta[delta["delta"] > 0]
    print(f"\n🔺 得分上升 {len(rising)} 只:")
    if not rising.empty:
        print(rising.to_string())

    # 得分持平但已是高分的
    high_stable = delta[(delta["delta"] == 0) & (delta["score_new"] >= 7)]
    if not high_stable.empty:
        print(f"\n⚡ 保持 7+ 分 {len(high_stable)} 只:")
        print(high_stable.to_string())


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(description="观察池追踪器")
    sub = p.add_subparsers(dest="command", required=True)

    # 主升前夜观察池命令
    s_save = sub.add_parser("save", help="从最新 scan 结果提取观察池")
    s_save.add_argument("--min-score", type=int, default=3,
                        help="最低纳入得分 (默认 3)")
    s_save.set_defaults(func=cmd_save)

    s_update = sub.add_parser("update", help="只扫描观察池，生成今日快照")
    s_update.set_defaults(func=cmd_update)

    s_diff = sub.add_parser("diff", help="对比最近两次快照，找得分上升的票")
    s_diff.set_defaults(func=cmd_diff)

    # 龙头断板观察池命令
    s_save_dragon = sub.add_parser("save-dragon", help="从最新 dragon-scan 提取龙头观察池")
    s_save_dragon.add_argument("--min-score", type=int, default=7,
                               help="最低纳入得分 (默认 7)")
    s_save_dragon.set_defaults(func=cmd_save_dragon)

    s_update_dragon = sub.add_parser("update-dragon", help="只扫描龙头观察池，生成今日快照")
    s_update_dragon.set_defaults(func=cmd_update_dragon)

    s_diff_dragon = sub.add_parser("diff-dragon", help="对比最近两次龙头快照，找得分上升的票")
    s_diff_dragon.set_defaults(func=cmd_diff_dragon)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
