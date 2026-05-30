"""
统一每日调度脚本
========================================
每日 15:10 盘后依次运行4个策略，生成HTML报告，推送Telegram汇总。

运行顺序：
  1. 漏斗策略 (funnel_strategy)  — 全市场6层过滤
  2. LLM多源 (llm_multisource)  — 多源信号聚合
  3. 八步隔夜法 (overnight_8step) — 盘后定稿
  4. 主升浪 (main_uptrend)       — 趋势突破检测
  5. 生成统一HTML报告
  6. 推送Telegram汇总

用法：
  python -m strategies.run_daily_all
  python -m strategies.run_daily_all --skip-llm --skip-funnel
  python -m strategies.run_daily_all --only-notify
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
for _env_path in [Path('.env'), Path('strategies/llm_multisource/.env')]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))

PROJECT_ROOT = Path(__file__).parent.parent


def _run_step(name: str, cmd: list, cwd: str = None, timeout: int = 600) -> bool:
    logger.info(f"{'='*50}")
    logger.info(f"▶ 开始: {name}")
    logger.info(f"  命令: {' '.join(cmd)}")
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            cwd=cwd or str(PROJECT_ROOT),
            timeout=timeout,
        )
        elapsed = time.time() - start
        if result.returncode == 0:
            logger.info(f"✅ {name} 完成 ({elapsed:.0f}s)")
            if result.stdout:
                for line in result.stdout.strip().split('\n')[-5:]:
                    logger.info(f"  {line}")
            return True
        else:
            logger.error(f"❌ {name} 失败 (exit={result.returncode}, {elapsed:.0f}s)")
            if result.stderr:
                for line in result.stderr.strip().split('\n')[-5:]:
                    logger.error(f"  {line}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"❌ {name} 超时 ({timeout}s)")
        return False
    except Exception as e:
        logger.error(f"❌ {name} 异常: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="OpenClaw 每日统一调度")
    parser.add_argument("--skip-funnel", action="store_true", help="跳过漏斗策略")
    parser.add_argument("--skip-llm", action="store_true", help="跳过LLM多源")
    parser.add_argument("--skip-8step", action="store_true", help="跳过八步隔夜法")
    parser.add_argument("--skip-uptrend", action="store_true", help="跳过主升浪")
    parser.add_argument("--skip-html", action="store_true", help="跳过HTML生成")
    parser.add_argument("--skip-notify", action="store_true", help="跳过Telegram推送")
    parser.add_argument("--only-notify", action="store_true", help="仅推送（不运行策略）")
    args = parser.parse_args()

    trade_date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info(f"OpenClaw 每日调度 — {trade_date}")
    logger.info("=" * 60)

    results = {}

    if args.only_notify:
        logger.info("仅推送模式，跳过策略运行")
    else:
        # 1. 漏斗策略
        if not args.skip_funnel:
            ok = _run_step(
                "漏斗策略",
                [sys.executable, "-m", "strategies.funnel_strategy.run_funnel", "--funnel"],
                timeout=300,
            )
            results["漏斗"] = ok

        # 2. LLM多源（不支持--date，自动获取今天日期，需在15:10后运行）
        if not args.skip_llm:
            ok = _run_step(
                "LLM多源",
                [sys.executable, "-m", "strategies.llm_multisource.aggregate"],
                timeout=600,
            )
            results["LLM多源"] = ok

        # 3. 八步隔夜法（不支持--date，自动获取今天日期，需在15:10后运行）
        if not args.skip_8step:
            ok = _run_step(
                "八步隔夜法",
                [sys.executable, "strategies/overnight_8step/zuiyou1.py"],
                timeout=300,
            )
            results["八步隔夜法"] = ok

        # 4. 主升浪
        if not args.skip_uptrend:
            ok = _run_step(
                "主升浪",
                [sys.executable, "-m", "strategies.main_uptrend.run_daily"],
                timeout=300,
            )
            results["主升浪"] = ok

    # 5. 生成HTML报告
    if not args.skip_html and not args.only_notify:
        ok1 = _run_step(
            "统一HTML报告(漏斗+4策略)",
            [sys.executable, "-m", "strategies.funnel_strategy.generate_html"],
            timeout=120,
        )
        results["统一HTML"] = ok1

        ok2 = _run_step(
            "LLM多源HTML报告",
            [sys.executable, "-m", "strategies.llm_multisource.generate_report"],
            timeout=120,
        )
        results["LLM HTML"] = ok2

    # 6. 推送Telegram汇总
    if not args.skip_notify:
        logger.info(f"{'='*50}")
        logger.info("▶ 推送Telegram汇总")
        try:
            from strategies.shared.daily_notify import push_daily_summary
            ok = push_daily_summary(trade_date)
            results["Telegram推送"] = ok
            logger.info(f"{'✅' if ok else '⚠️'} Telegram推送{'成功' if ok else '失败/未配置'}")
        except Exception as e:
            logger.error(f"❌ Telegram推送异常: {e}")
            results["Telegram推送"] = False

    # 汇总
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"调度汇总 — {trade_date}")
    logger.info("=" * 60)
    for name, ok in results.items():
        logger.info(f"  {'✅' if ok else '❌'} {name}")

    all_ok = all(results.values()) if results else True
    logger.info(f"\n整体结果: {'✅ 全部成功' if all_ok else '⚠️ 部分失败'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
