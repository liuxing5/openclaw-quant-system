"""
主入口 — OpenClaw 策略集
================================================

策略 1 — 主升前夜 (周月级深度回撤后的启动):
  python main.py scan                  # 全市场扫描
  python main.py scan --sample         # 样本验证
  python main.py backtest --start 2024-01-01 --end 2024-12-31

策略 2 — 龙头断板 (板块热度中的短线切入):
  python main.py dragon-scan           # 全市场扫描
  python main.py dragon-scan --sample  # 样本验证
  python main.py dragon-backtest --start 2024-01-01 --end 2024-12-31

通用:
  python main.py test                  # 冒烟测试

输出:
  ./results/scan_YYYYMMDD.csv          # 主升前夜
  ./results/dragon_scan_YYYYMMDD.csv   # 龙头断板
  ./logs/strategy_YYYYMMDD.log         # 详细日志
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from config import (
    ScreenerConfig, ExitorConfig, BacktestConfig, DataConfig,
    DragonConfig, TZ
)
from data_loader import DataLoader
from screener import scan_universe
from dragon_screener import scan_universe_dragon, precompute_sector_sync
from backtester import WalkForwardBacktester


# ============================================================
def setup_logging(log_dir: str = "./logs"):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / f"strategy_{datetime.now(TZ).strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ============================================================
def cmd_scan(args):
    logger = logging.getLogger("scan")
    
    # T+1 时间检查：必须在 15:00 后运行
    now = datetime.now(TZ)
    if now.hour < 15:
        logger.warning(f"⚠️  当前时间 {now.strftime('%H:%M')} < 15:00, 当日数据未生成!")
        logger.warning("将使用昨日数据扫描，T+1 逻辑可能不准确")
        logger.warning("建议：每日 15:30 后执行扫描")
    
    loader = DataLoader()

    # 选择标的池
    if args.sample:
        symbols = [
            ("600519", "贵州茅台"),
            ("000001", "平安银行"),
            ("300750", "宁德时代"),
            ("002594", "比亚迪"),
            ("000858", "五粮液"),
            ("600036", "招商银行"),
            ("601318", "中国平安"),
            ("000333", "美的集团"),
            ("002415", "海康威视"),
            ("600276", "恒瑞医药"),
        ]
        logger.info(f"使用样本池 {len(symbols)} 只")
    else:
        all_stocks = loader.get_all_stocks()
        if all_stocks is None or all_stocks.empty:
            logger.error("全市场列表获取失败,退出")
            return
        df = all_stocks.copy()

        # === A 股普通股的代码段白名单(过滤 ETF/基金/可转债/优先股等)===
        # 主板:600/601/603/605 (沪) + 000/001/002/003 (深)
        # 创业板:300/301
        # 科创板:688
        # 排除:51x/15x/16x/50x (ETF/基金), 11x/12x (可转债), 9xx (B股), 4xx/8xx (北交所)
        valid_prefixes = ("600", "601", "603", "605", "000", "001",
                          "002", "003", "300", "301", "688")
        before = len(df)
        df = df[df["symbol"].astype(str).str.startswith(valid_prefixes)]
        logger.info(f"代码段白名单过滤: {before} -> {len(df)} (剔除 {before - len(df)} 只非普通A股)")

        if "name" in df.columns:
            df = df[~df["name"].str.contains("ST|退", regex=True, na=False)]

        symbols = list(zip(df["symbol"].astype(str), df["name"]))
        logger.info(f"全市场过滤后 {len(symbols)} 只(注意: baostock 无流通市值, "
                    f"建议先用 --sample 验证, 全市场扫描需要较长时间)")

    # 扫描
    cfg = ScreenerConfig()
    result = scan_universe(symbols, cfg=cfg, loader=loader)

    # 输出 - 用绝对路径,并打印实际写入位置
    out_dir = Path("./results").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TZ).strftime("%Y%m%d")
    out_path = out_dir / f"scan_{today}.csv"

    try:
        result.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n✓ 扫描结果已写入: {out_path}")
        print(f"  文件大小: {out_path.stat().st_size:,} 字节, 共 {len(result)} 行")
    except Exception as e:
        print(f"\n✗ 写入失败: {e}")
        print(f"  目标路径: {out_path}")
        # 备份方案: 写到 home 目录
        backup = Path.home() / f"scan_{today}.csv"
        try:
            result.to_csv(backup, index=False, encoding="utf-8-sig")
            print(f"  已写到备份位置: {backup}")
        except Exception as e2:
            print(f"  备份也失败: {e2}")
            print(f"  result DataFrame 列: {list(result.columns)}")
            print(f"  result 前 3 行:\n{result.head(3)}")
            return

    triggered = result[result["triggered"] == True] if "triggered" in result.columns else result
    print(f"\n触发标的: {len(triggered)} 只\n")
    if not triggered.empty:
        cols = [c for c in ["symbol", "name", "score", "last_close",
                             "suggested_stop"] if c in triggered.columns]
        print(triggered[cols].head(20).to_string(index=False))

    # near-miss 模式: 显示"接近触发"的标的(下一周可能触发)
    # 默认看 5+ 分,如果数据中没有 5+ 分的票,自动降阈值显示得分最高的若干只
    if args.show_near_miss and "score" in result.columns:
        target = result["triggered"] == False if "triggered" in result.columns else True
        candidates = result[target]
        max_score = candidates["score"].max() if not candidates.empty else 0

        # 自适应阈值: 优先看 ≥5 分的票,没有就降到 max_score 或 max_score-1
        if (candidates["score"] >= 5).any():
            near_threshold = 5
        elif max_score >= 1:
            near_threshold = max(1, max_score - 1)
        else:
            near_threshold = 0

        near = candidates[
            (candidates["score"] >= near_threshold)
            & (candidates["score"] < cfg.min_layers_to_trigger)
        ].sort_values("score", ascending=False)

        print(f"\n--- 接近触发(得分 {near_threshold} ~ {cfg.min_layers_to_trigger - 1} 分) ---")
        print(f"共 {len(near)} 只(数据中最高得分 {int(max_score)} 分)\n")
        if not near.empty:
            cols = [c for c in ["symbol", "name", "score", "last_close", "last_fail"]
                    if c in near.columns]
            print(near[cols].head(30).to_string(index=False))

    # 即使没用 --diagnose,0 触发时强制输出诊断信息
    if len(triggered) == 0 and not args.diagnose:
        print("\n" + "=" * 60)
        print("⚠ 0 触发,强制输出诊断信息(避免你猜哪层有问题)")
        print("=" * 60)
        if "score" in result.columns:
            print(f"\n得分分布:")
            print(result["score"].value_counts().sort_index().to_string())
            top5 = result.nlargest(5, "score")
            print(f"\n得分最高的 5 只:")
            cols = ["symbol", "name", "score"] + \
                   [c for c in result.columns if c.startswith("L")] + \
                   ["last_fail"]
            cols = [c for c in cols if c in result.columns]
            print(top5[cols].to_string(index=False))

        # 各层失败次数
        layer_cols = [c for c in result.columns if c.startswith("L") and c != "last_fail"]
        if layer_cols:
            print(f"\n各层失败次数(失败最多的层是当前最严苛的瓶颈):")
            fail_counts = {col: int((result[col] == "✗").sum()) for col in layer_cols}
            max_fail = max(fail_counts.values(), default=1)
            for layer, n in sorted(fail_counts.items(), key=lambda x: -x[1]):
                bar = "█" * int(n / max_fail * 30)
                print(f"  {layer:8s} {n:4d} 次  {bar}")

        # last_fail 的 top 原因
        if "last_fail" in result.columns:
            print(f"\nlast_fail 出现次数最多的 10 个原因:")
            top_reasons = result["last_fail"].value_counts().head(10)
            for reason, n in top_reasons.items():
                print(f"  {n:4d} 次: {reason}")

    # 诊断模式: 输出每只票卡在哪一层
    if args.diagnose:
        print("\n" + "=" * 60)
        print("诊断模式: 每只标的的层级通过情况")
        print("=" * 60)
        layer_cols = [c for c in result.columns if c.startswith("L")]
        diag_cols = ["symbol", "name", "score", "triggered"] + layer_cols + ["last_fail"]
        diag_cols = [c for c in diag_cols if c in result.columns]
        print(result[diag_cols].to_string(index=False))

        # 每层失败次数统计
        print("\n--- 各层失败次数(快速定位最严苛的层)---")
        fail_counts = {}
        for col in layer_cols:
            fail_count = (result[col] == "✗").sum()
            fail_counts[col] = fail_count
        for layer, n in sorted(fail_counts.items(), key=lambda x: -x[1]):
            bar = "█" * int(n / max(fail_counts.values(), default=1) * 30)
            print(f"  {layer:6s} 失败 {n:3d} 次 {bar}")

        print("\n建议: 失败次数最高的层是当前最严苛的瓶颈,可在 ScreenerConfig 中放宽阈值")


# ============================================================
def cmd_backtest(args):
    logger = logging.getLogger("backtest")
    loader = DataLoader()

    # 标的池(回测建议先用样本验证)
    if args.sample:
        symbols = [
            ("600519", "贵州茅台"),
            ("000001", "平安银行"),
            ("300750", "宁德时代"),
            ("002594", "比亚迪"),
            ("600036", "招商银行"),
            ("000333", "美的集团"),
            ("002415", "海康威视"),
            ("600276", "恒瑞医药"),
            ("601318", "中国平安"),
            ("000858", "五粮液"),
        ]
    else:
        all_stocks = loader.get_all_stocks()
        if all_stocks is None or all_stocks.empty:
            logger.error("全市场列表获取失败,退出")
            return
        df = all_stocks.copy()

        # 代码段白名单(同 scan)
        valid_prefixes = ("600", "601", "603", "605", "000", "001",
                          "002", "003", "300", "301", "688")
        df = df[df["symbol"].astype(str).str.startswith(valid_prefixes)]

        if "name" in df.columns:
            df = df[~df["name"].str.contains("ST|退", regex=True, na=False)]

        # 回测全市场太慢,默认取前 N 只
        symbols = list(zip(df["symbol"].astype(str), df["name"]))[:args.universe_limit]

    logger.info(f"回测标的 {len(symbols)} 只")

    bt_cfg = BacktestConfig()
    bt = WalkForwardBacktester(
        symbols, bt_cfg=bt_cfg, loader=loader
    )
    result = bt.run(args.start, args.end)

    # 输出
    out_dir = Path("./results")
    out_dir.mkdir(parents=True, exist_ok=True)

    if not result.equity_curve.empty:
        result.equity_curve.to_csv(out_dir / "equity_curve.csv",
                                    encoding="utf-8-sig")
    if not result.trades.empty:
        result.trades.to_csv(out_dir / "backtest_trades.csv",
                              index=False, encoding="utf-8-sig")
    with open(out_dir / "backtest_metrics.json", "w", encoding="utf-8") as f:
        json.dump(result.metrics, f, indent=2, ensure_ascii=False, default=str)

    print("\n" + "=" * 60)
    print("回测结果")
    print("=" * 60)
    for k, v in result.metrics.items():
        if isinstance(v, float):
            if k in ("total_return", "cagr", "volatility",
                     "max_drawdown", "win_rate"):
                print(f"  {k:20s}: {v:>10.2%}")
            else:
                print(f"  {k:20s}: {v:>10.4f}")
        else:
            print(f"  {k:20s}: {v}")
    print(f"\n输出目录: {out_dir.absolute()}")


# ============================================================
def cmd_dragon_scan(args):
    logger = logging.getLogger("dragon_scan")
    
    # T+1 时间检查：必须在 15:00 后运行
    now = datetime.now(TZ)
    if now.hour < 15:
        logger.warning(f"⚠️  当前时间 {now.strftime('%H:%M')} < 15:00, 当日数据未生成!")
        logger.warning("将使用昨日数据扫描，T+1 逻辑可能不准确")
        logger.warning("建议：每日 15:30 后执行扫描")
    
    loader = DataLoader()
    today = datetime.now(TZ).strftime("%Y-%m-%d")

    # 选择标的池
    if args.sample:
        symbols = [
            ("600519", "贵州茅台"),
            ("000001", "平安银行"),
            ("300750", "宁德时代"),
            ("002594", "比亚迪"),
            ("000858", "五粮液"),
            ("600036", "招商银行"),
            ("601318", "中国平安"),
            ("000333", "美的集团"),
            ("002415", "海康威视"),
            ("600276", "恒瑞医药"),
        ]
        logger.info(f"龙头断板 — 样本池 {len(symbols)} 只")
        sector_sync_map = {}  # 样本模式不计算板块同步度
    else:
        all_stocks = loader.get_all_stocks()
        if all_stocks is None or all_stocks.empty:
            logger.error("全市场列表获取失败")
            return
        df = all_stocks.copy()
        valid_prefixes = ("600", "601", "603", "605", "000", "001",
                          "002", "003", "300", "301", "688")
        before = len(df)
        df = df[df["symbol"].astype(str).str.startswith(valid_prefixes)]
        logger.info(f"代码段白名单过滤：{before} -> {len(df)} (剔除 {before - len(df)} 只非普通 A 股)")
        
        if "name" in df.columns:
            df = df[~df["name"].str.contains("ST|退", regex=True, na=False)]
        
        # === 预扫描抽样：随机 + 代码段均衡 (用于 L4 板块同步度计算) ===
        # 优化：减少抽样数量 (500→300)，提升预计算速度
        # 目标：抽 300 只，覆盖各代码段 (主板/创业板/科创板)
        all_symbols = list(zip(df["symbol"].astype(str), df["name"]))
        
        # 按代码段分组
        segments = {
            "main": [],      # 600/601/603/605/000/001/002/003
            "chinext": [],   # 300/301
            "star": [],      # 688
        }
        for sym, name in all_symbols:
            if sym.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
                segments["main"].append((sym, name))
            elif sym.startswith(("300", "301")):
                segments["chinext"].append((sym, name))
            elif sym.startswith("688"):
                segments["star"].append((sym, name))
        
        # 配额抽样 (保持市场结构)
        import random
        random.seed(42)  # 可复现
        
        sample_size = min(300, len(all_symbols))  # 优化：500→300
        quota = {
            "main": int(sample_size * 0.7),      # 70% 主板
            "chinext": int(sample_size * 0.2),   # 20% 创业板
            "star": int(sample_size * 0.1),      # 10% 科创板
        }
        
        sampled = []
        for seg, count in quota.items():
            available = len(segments[seg])
            take = min(count, available)
            sampled.extend(random.sample(segments[seg], take))
        
        # 如果配额没抽满，用剩余的补
        if len(sampled) < sample_size:
            remaining = [s for s in all_symbols if s not in sampled]
            need = sample_size - len(sampled)
            if remaining:
                sampled.extend(random.sample(remaining, min(need, len(remaining))))
        
        logger.info(f"预扫描抽样：{len(sampled)} 只 (主板{len(segments['main'])} 选{quota['main']}, "
                   f"创业板{len(segments['chinext'])} 选{quota['chinext']}, "
                   f"科创板{len(segments['star'])} 选{quota['star']})")
        
        # 预计算板块同步度 (用抽样数据)
        logger.info("预计算板块同步度 (L4)...")
        # 注意：precompute_sector_sync 内部调用 get_kline，需要 YYYYMMDD 格式
        sector_sync_map = precompute_sector_sync(
            sampled, loader=loader, days=30, end_date=datetime.now(TZ).strftime("%Y%m%d")
        )
        logger.info(f"板块同步度：涵盖 {len(sector_sync_map)} 个交易日")
        if sector_sync_map:
            top_days = sorted(sector_sync_map.items(), key=lambda x: -x[1])[:5]
            logger.info(f"同步涨停数前 5: {top_days}")
        else:
            logger.warning("近期无板块同步涨停现象 (市场低迷或样本不足)")
        
        symbols = all_symbols
        logger.info(f"龙头断板 — 全市场 {len(symbols)} 只")

    # 扫描
    cfg = DragonConfig()
    result = scan_universe_dragon(symbols, cfg=cfg, loader=loader, 
                                   sector_sync=sector_sync_map, eval_date=today)

    # 输出
    out_dir = Path("./results").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TZ).strftime("%Y%m%d")
    out_path = out_dir / f"dragon_scan_{today}.csv"

    try:
        result.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n✓ 龙头断板扫描已写入: {out_path}")
        print(f"  文件大小: {out_path.stat().st_size:,} 字节, 共 {len(result)} 行")
    except Exception as e:
        print(f"\n✗ 写入失败: {e}")
        return

    triggered = result[result["triggered"] == True] if "triggered" in result.columns else result
    print(f"\n触发标的: {len(triggered)} 只\n")
    if not triggered.empty:
        cols = [c for c in ["symbol", "name", "score", "last_close",
                             "break_board_date", "break_board_close",
                             "suggested_entry", "suggested_stop"]
                if c in triggered.columns]
        print(triggered[cols].head(20).to_string(index=False))

    # 0 触发时强制诊断
    if len(triggered) == 0:
        print("\n" + "=" * 60)
        print("⚠ 0 触发 — 诊断信息")
        print("=" * 60)
        if "score" in result.columns and not result.empty:
            print(f"\n得分分布:")
            print(result["score"].value_counts().sort_index().to_string())
            top5 = result.nlargest(5, "score")
            cols = [c for c in ["symbol", "name", "score"] +
                    [c for c in result.columns if c.startswith("L")] +
                    ["last_fail"] if c in result.columns]
            print(f"\n得分最高的 5 只:")
            print(top5[cols].to_string(index=False))

            layer_cols = [c for c in result.columns
                          if c.startswith("L") and c != "last_fail"]
            if layer_cols:
                print(f"\n各层失败次数:")
                fail_counts = {col: int((result[col] == "✗").sum())
                               for col in layer_cols}
                max_fail = max(fail_counts.values(), default=1)
                for layer, n in sorted(fail_counts.items(), key=lambda x: -x[1]):
                    bar = "█" * int(n / max_fail * 30)
                    print(f"  {layer:6s} {n:4d} 次  {bar}")


# ============================================================
def cmd_dragon_backtest(args):
    """龙头断板回测(用通用回测器,传入 DragonScreener)"""
    logger = logging.getLogger("dragon_backtest")
    logger.info("龙头断板回测功能尚未完全集成到 backtester, "
                "建议先跑扫描观察触发频率和标的分布")
    logger.info("计划: 下个版本让 WalkForwardBacktester 支持 --strategy dragon")


# ============================================================
def cmd_test(args):
    """跑冒烟测试套件"""
    import test_smoke
    test_smoke.test_indicators()
    test_smoke.test_screener_with_mock()
    test_smoke.test_exitor()
    test_smoke.test_portfolio_and_risk()
    test_smoke.test_backtester_e2e()
    print("\n✅ 所有测试通过")


# ============================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="OpenClaw 主升前夜策略 (baostock 数据源)",
        epilog="示例:\n"
               "  python main.py test                                    # 离线冒烟测试\n"
               "  python main.py scan --sample                           # 扫描样本(快)\n"
               "  python main.py scan                                    # 全市场扫描(慢)\n"
               "  python main.py backtest --sample --start 2024-06-01 --end 2024-12-31",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command")  # 不强制 required,留出友好提示空间

    s_scan = sub.add_parser("scan", help="扫描当日信号")
    s_scan.add_argument("--sample", action="store_true",
                        help="仅扫描样本池(快速)")
    s_scan.add_argument("--diagnose", action="store_true",
                        help="诊断模式: 打印每只票卡在哪一层, 统计各层失败次数")
    s_scan.add_argument("--show-near-miss", action="store_true",
                        help="显示得分 5+ 但未触发的'接近候选',作为观察池")
    s_scan.set_defaults(func=cmd_scan)

    s_bt = sub.add_parser("backtest", help="历史回测")
    s_bt.add_argument("--start", default="2024-01-01")
    s_bt.add_argument("--end", default="2024-12-31")
    s_bt.add_argument("--sample", action="store_true",
                      help="仅用样本池回测")
    s_bt.add_argument("--universe-limit", type=int, default=200,
                      help="全市场回测时的最大标的数")
    s_bt.set_defaults(func=cmd_backtest)

    s_test = sub.add_parser("test", help="冒烟测试(无网,可在没有 baostock 时运行)")
    s_test.set_defaults(func=cmd_test)

    # ---------- 龙头断板策略 ----------
    s_dscan = sub.add_parser("dragon-scan", help="龙头断板扫描(短线,日度)")
    s_dscan.add_argument("--sample", action="store_true",
                         help="仅扫描样本池(快速)")
    s_dscan.set_defaults(func=cmd_dragon_scan)

    s_dbt = sub.add_parser("dragon-backtest", help="龙头断板回测(开发中)")
    s_dbt.add_argument("--start", default="2024-01-01")
    s_dbt.add_argument("--end", default="2024-12-31")
    s_dbt.set_defaults(func=cmd_dragon_backtest)

    return p


# ============================================================
if __name__ == "__main__":
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        print("\n请提供子命令。可用选项:\n")
        print("  === 主升前夜策略(中线, 周度) ===")
        print("  python main.py scan --sample                 # 扫描 10 只样本")
        print("  python main.py scan                          # 全市场扫描")
        print("  python main.py backtest --sample --start 2024-06-01 --end 2024-12-31")
        print()
        print("  === 龙头断板策略(短线, 日度) ===")
        print("  python main.py dragon-scan --sample          # 扫描 10 只样本")
        print("  python main.py dragon-scan                   # 全市场扫描")
        print()
        print("  === 通用 ===")
        print("  python main.py test                          # 冒烟测试")
        print("\n更多帮助: python main.py -h\n")
        sys.exit(1)
    args.func(args)
