"""
实盘运行入口 — 5策略共振 + LLM多源 + 八步法
================================================
三层筛选架构：
  第1层：5策略共振过滤（20周线/均线多头/MACD/布林/年线）
  第2层：LLM多源策略（新闻资讯/研报/公告/龙虎榜）
  第3层：隔夜八步法（量价精选）

运行时间：
  盘后：15:10+  CONFIG["MODE"] = "post"

止损铁律：
  稳健路径：次日09:35未维持昨收+1%，直接出局
  高位路径：次日竞价弱于昨收，集合竞价结束即清仓
  全局止损：亏损超2.5%无条件止损

使用示例：
  # 完整三层筛选（从数据库加载LLM候选）
  python run_resonance_strategy.py --llm-db

  # 仅共振+八步法（跳过LLM层）
  python run_resonance_strategy.py

  # 从CSV文件加载LLM候选
  python run_resonance_strategy.py --llm-csv ./llm_results.csv

  # 指定交易日期
  python run_resonance_strategy.py --date 2026-05-12

  # 禁用年线和布林带过滤
  python run_resonance_strategy.py --no-annual --no-bollinger
"""
from __future__ import annotations

import sys
import os
import argparse
from datetime import datetime, date
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from strategies.combine_strategies import run_resonance_strategy


def main():
    parser = argparse.ArgumentParser(
        description="5策略共振 + LLM多源 + 八步法 实盘运行入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 完整三层筛选（从数据库加载LLM候选）
  python run_resonance_strategy.py --llm-db

  # 仅共振+八步法（跳过LLM层）
  python run_resonance_strategy.py

  # 从CSV文件加载LLM候选
  python run_resonance_strategy.py --llm-csv ./llm_results.csv

  # 指定交易日期
  python run_resonance_strategy.py --date 2026-05-12

  # 禁用年线和布林带过滤
  python run_resonance_strategy.py --no-annual --no-bollinger
        """
    )

    parser.add_argument("--date", "-d", type=str, default=None,
                        help="交易日期（YYYY-MM-DD），默认最新交易日")
    parser.add_argument("--output", "-o", type=str, default="./results",
                        help="输出目录（默认 ./results）")

    # LLM候选输入选项
    llm_group = parser.add_mutually_exclusive_group()
    llm_group.add_argument("--llm-db", action="store_true",
                           help="从数据库加载LLM多源策略候选")
    llm_group.add_argument("--llm-csv", type=str, default=None,
                           help="从CSV文件加载LLM候选（指定文件路径）")

    # 共振过滤参数
    parser.add_argument("--min-pass", "-m", type=int, default=3,
                        help="最少通过的共振策略数量（默认3）")
    parser.add_argument("--no-core", action="store_true",
                        help="不要求核心3策略必须全部通过")
    parser.add_argument("--no-annual", action="store_true",
                        help="禁用年线过滤")
    parser.add_argument("--no-bollinger", action="store_true",
                        help="禁用布林带过滤")

    # LLM分数阈值
    parser.add_argument("--llm-score", type=int, default=25,
                        help="LLM最低分数阈值（默认25）")

    # 调试模式
    parser.add_argument("--debug", action="store_true",
                        help="调试模式（打印详细日志）")

    args = parser.parse_args()

    # 解析交易日期
    trade_date = None
    if args.date:
        try:
            trade_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"❌ 日期格式错误: {args.date}，应为 YYYY-MM-DD")
            sys.exit(1)

    # 解析LLM输入
    llm_input = None
    if args.llm_db:
        llm_input = 'db'
    elif args.llm_csv:
        if not Path(args.llm_csv).exists():
            print(f"❌ LLM候选文件不存在: {args.llm_csv}")
            sys.exit(1)
        llm_input = args.llm_csv

    # 打印运行配置
    print("\n" + "=" * 70)
    print("  5策略共振 + LLM多源 + 八步法 实盘运行")
    print("=" * 70)
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  交易日期: {trade_date if trade_date else '最新交易日'}")
    print(f"  输出目录: {args.output}")
    print(f"  LLM输入: {llm_input if llm_input else '无（跳过LLM层）'}")
    print(f"  共振过滤: 最少通过 {args.min_pass} 个策略")
    if args.no_core:
        print(f"  ⚠️ 不要求核心3策略必须通过")
    if args.no_annual:
        print(f"  ⚠️ 禁用年线过滤")
    if args.no_bollinger:
        print(f"  ⚠️ 禁用布林带过滤")
    print("=" * 70 + "\n")

    # 运行策略
    try:
        results = run_resonance_strategy(
            llm_input=llm_input,
            output_dir=args.output,
            trade_date=trade_date,
            min_pass_count=args.min_pass,
            require_core=not args.no_core,
            enable_annual_line=not args.no_annual,
            enable_bollinger=not args.no_bollinger,
            llm_min_score=args.llm_score,
        )

        if results:
            print(f"\n✅ 运行成功! 入选 {len(results)} 只标的")
            print(f"📄 结果文件: {Path(args.output).resolve() / f'resonance_{datetime.now().strftime('%Y%m%d')}.csv'}")
        else:
            print("\n❌ 没有标的通过筛选")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断运行")
        sys.exit(130)
    except Exception as e:
        print(f"\n\n❌ 运行失败: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
