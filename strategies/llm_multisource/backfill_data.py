"""历史数据补全脚本 - 回填新增表和字段

补全内容：
1. daily_quotes 新增字段 (amplitude, volume_ratio, commission_ratio, large_order_net, main_force_net)
2. strong_stock_rank 表历史数据
3. earnings_forecast 表数据
4. concept_board_quotes 表历史数据
"""
import os
import sys
import time
from datetime import date, timedelta, datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.db.connection import get_db
from core.utils.trading_calendar import is_trading_day
from strategies.llm_multisource.fetchers.layer1_market import (
    fetch_tencent_supplementary,
    fetch_ths_strong_stocks_structured,
    fetch_concept_board_quotes,
    fetch_earnings_forecast_structured,
)
from strategies.llm_multisource.store_structured import (
    update_tencent_quotes,
    store_strong_stock_rank,
    store_concept_board_quotes,
    store_earnings_forecast,
)
from loguru import logger

BEIJING_TZ = timezone(timedelta(hours=8))


def make_signal_dummy(source, title, content='', url='', pub_time=None, tier=2):
    """Dummy signal factory for backfill (we only need structured data)"""
    return []


def backfill_tencent_quotes(days: int = 5):
    """回填 Tencent 行情补充数据到 daily_quotes"""
    logger.info(f"开始回填 Tencent 行情数据，最近 {days} 天")
    
    today = datetime.now(BEIJING_TZ).date()
    
    for i in range(days):
        target_date = today - timedelta(days=i)
        if not is_trading_day(target_date):
            logger.info(f"{target_date} 非交易日，跳过")
            continue
        
        logger.info(f"回填 {target_date} Tencent 数据...")
        try:
            rows = fetch_tencent_supplementary(make_signal_dummy)
            if hasattr(rows, '_tencent_data') and rows._tencent_data:
                update_tencent_quotes(rows._tencent_data)
                logger.info(f"  写入 {len(rows._tencent_data)} 条")
            time.sleep(1)
        except Exception as e:
            logger.warning(f"  {target_date} 回填失败: {e}")


def backfill_strong_stock_rank(days: int = 5):
    """回填 THS 强势股排名数据"""
    logger.info(f"开始回填强势股排名数据，最近 {days} 天")
    
    today = datetime.now(BEIJING_TZ).date()
    
    for i in range(days):
        target_date = today - timedelta(days=i)
        if not is_trading_day(target_date):
            logger.info(f"{target_date} 非交易日，跳过")
            continue
        
        logger.info(f"回填 {target_date} 强势股数据...")
        try:
            rows = fetch_ths_strong_stocks_structured(make_signal_dummy)
            if hasattr(rows, '_strong_stock_rank') and rows._strong_stock_rank:
                store_strong_stock_rank(rows._strong_stock_rank)
                logger.info(f"  写入 {len(rows._strong_stock_rank)} 条")
            time.sleep(1)
        except Exception as e:
            logger.warning(f"  {target_date} 回填失败: {e}")


def backfill_concept_board_quotes(days: int = 5):
    """回填 THS 概念板块行情数据"""
    logger.info(f"开始回填概念板块数据，最近 {days} 天")
    
    today = datetime.now(BEIJING_TZ).date()
    
    for i in range(days):
        target_date = today - timedelta(days=i)
        if not is_trading_day(target_date):
            logger.info(f"{target_date} 非交易日，跳过")
            continue
        
        logger.info(f"回填 {target_date} 概念板块数据...")
        try:
            rows = fetch_concept_board_quotes(make_signal_dummy)
            if hasattr(rows, '_concept_board_quotes') and rows._concept_board_quotes:
                store_concept_board_quotes(rows._concept_board_quotes)
                logger.info(f"  写入 {len(rows._concept_board_quotes)} 条")
            time.sleep(1)
        except Exception as e:
            logger.warning(f"  {target_date} 回填失败: {e}")


def backfill_earnings_forecast():
    """回填机构一致预期数据（只需一次，非时间敏感）"""
    logger.info("开始回填机构一致预期数据...")
    try:
        rows = fetch_earnings_forecast_structured(make_signal_dummy)
        if hasattr(rows, '_earnings_forecast') and rows._earnings_forecast:
            store_earnings_forecast(rows._earnings_forecast)
            logger.info(f"  写入 {len(rows._earnings_forecast)} 条")
    except Exception as e:
        logger.warning(f"  回填失败: {e}")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='历史数据补全脚本')
    parser.add_argument('--days', type=int, default=5, help='回填天数 (default: 5)')
    parser.add_argument('--tencent', action='store_true', help='只回填 Tencent 数据')
    parser.add_argument('--strong', action='store_true', help='只回填强势股数据')
    parser.add_argument('--concept', action='store_true', help='只回填概念板块数据')
    parser.add_argument('--earnings', action='store_true', help='只回填机构预期数据')
    parser.add_argument('--all', action='store_true', help='回填所有数据')
    
    args = parser.parse_args()
    
    if not any([args.tencent, args.strong, args.concept, args.earnings, args.all]):
        args.all = True
    
    logger.info("=" * 60)
    logger.info("历史数据补全开始")
    logger.info("=" * 60)
    
    if args.all or args.tencent:
        backfill_tencent_quotes(args.days)
    
    if args.all or args.strong:
        backfill_strong_stock_rank(args.days)
    
    if args.all or args.concept:
        backfill_concept_board_quotes(args.days)
    
    if args.all or args.earnings:
        backfill_earnings_forecast()
    
    logger.info("=" * 60)
    logger.info("历史数据补全完成")
    logger.info("=" * 60)
