#!/usr/bin/env python3
"""
每日数据更新脚本
增量更新全A股最新交易数据，自动运行于每个交易日收盘后
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import json
import traceback
from typing import List, Dict, Any, Optional
import warnings
warnings.filterwarnings('ignore')

# 添加路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.database_manager import DatabaseManager
from database.backfill_all_stocks import HistoricalDataBackfiller

class DailyDataUpdater:
    """每日数据更新器"""
    
    def __init__(self, max_workers: int = 8):
        self.db = DatabaseManager()
        self.backfiller = HistoricalDataBackfiller(max_workers=max_workers)
        
        # 交易日判断
        self.trading_days = self._load_trading_calendar()
        
        # 更新配置
        self.config = {
            'update_window': 5,  # 更新最近5天（防止假期遗漏）
            'retry_times': 3,     # 重试次数
            'retry_delay': 10,    # 重试延迟（秒）
            'timeout_per_stock': 60,  # 单股票超时（秒）
            'batch_size': 100,    # 批处理大小
            'skip_weekend': True, # 跳过周末
            'skip_holiday': True  # 跳过节假日
        }
    
    def _load_trading_calendar(self) -> List[str]:
        """加载交易日历（简化版，实际应从交易所获取）"""
        # 这里使用简单逻辑：排除周末
        # 实际应用中应接入交易所交易日历API
        
        # 生成最近一年的日期
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)
        
        trading_days = []
        current = start_date
        while current <= end_date:
            # 排除周末（简化处理）
            if current.weekday() < 5:  # 0-4为周一到周五
                trading_days.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)
        
        return trading_days
    
    def is_trading_day(self, date_str: str = None) -> bool:
        """判断是否为交易日"""
        if date_str is None:
            date_str = datetime.now().strftime('%Y-%m-%d')
        
        # 检查是否周末
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        if date_obj.weekday() >= 5:  # 周六、周日
            return False
        
        # 检查是否在交易日历中（简化）
        # 实际应检查节假日
        
        return True
    
    def get_last_trading_day(self, date_str: str = None) -> str:
        """获取上一个交易日"""
        if date_str is None:
            date_str = datetime.now().strftime('%Y-%m-%d')
        
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        
        # 向前查找交易日
        for i in range(1, 10):  # 最多向前找10天
            prev_date = date_obj - timedelta(days=i)
            prev_date_str = prev_date.strftime('%Y-%m-%d')
            
            if self.is_trading_day(prev_date_str):
                return prev_date_str
        
        return date_str  # 如果没找到，返回原日期
    
    def get_update_date_range(self) -> tuple:
        """获取需要更新的日期范围"""
        # 获取数据库中最后更新日期
        last_update = self.db.get_last_trading_date()
        
        if last_update is None:
            # 数据库为空，需要全量更新
            print("数据库为空，需要全量更新")
            start_date = (datetime.now() - timedelta(days=365 * 5)).strftime('%Y-%m-%d')
            end_date = datetime.now().strftime('%Y-%m-%d')
            return start_date, end_date, 'full'
        
        # 计算需要更新的开始日期（最后更新日期的后一天）
        last_dt = datetime.strptime(last_update, '%Y-%m-%d')
        start_dt = last_dt + timedelta(days=1)
        start_date = start_dt.strftime('%Y-%m-%d')
        
        # 结束日期为今天
        end_date = datetime.now().strftime('%Y-%m-%d')
        
        # 如果是同一天，且今天不是交易日，不需要更新
        if start_date > end_date or not self.is_trading_day():
            print(f"无需更新: 最后更新{last_update}, 今天{end_date}不是交易日或已更新")
            return None, None, 'skip'
        
        # 如果间隔较大，使用配置的更新窗口
        days_diff = (datetime.now() - last_dt).days
        if days_diff > self.config['update_window']:
            start_dt = datetime.now() - timedelta(days=self.config['update_window'])
            start_date = start_dt.strftime('%Y-%m-%d')
            print(f"更新间隔较大({days_diff}天)，使用最近{self.config['update_window']}天窗口")
        
        return start_date, end_date, 'incremental'
    
    def get_stocks_to_update(self) -> List[str]:
        """获取需要更新的股票列表"""
        # 获取所有活跃股票
        stocks = self.db.get_all_stocks(status='active')
        
        if not stocks:
            print("数据库中没有股票，使用默认股票池")
            return self.backfiller.default_symbols
        
        symbols = [stock['symbol'] for stock in stocks]
        
        # 过滤掉近期已更新的股票（简化处理）
        # 实际可以根据最后更新日期进行过滤
        
        print(f"获取到 {len(symbols)} 支活跃股票需要更新")
        return symbols
    
    def update_single_stock(self, symbol: str, start_date: str, end_date: str) -> Dict[str, Any]:
        """更新单只股票数据"""
        result = {
            'symbol': symbol,
            'success': False,
            'new_count': 0,
            'update_count': 0,
            'error': None
        }
        
        for attempt in range(self.config['retry_times']):
            try:
                print(f"  更新 {symbol} ({attempt+1}/{self.config['retry_times']})...")
                
                # 获取价格数据
                price_df = self.backfiller.fetch_daily_prices(symbol, start_date, end_date)
                
                if price_df is not None and not price_df.empty:
                    # 插入数据库
                    new_count, update_count = self.db.insert_daily_prices(symbol, price_df)
                    
                    result['success'] = True
                    result['new_count'] = new_count
                    result['update_count'] = update_count
                    
                    if new_count > 0 or update_count > 0:
                        print(f"    {symbol}: 新增{new_count}条, 更新{update_count}条")
                    else:
                        print(f"    {symbol}: 无新数据")
                    
                    break  # 成功，跳出重试循环
                else:
                    print(f"    {symbol}: 获取数据为空")
                    result['error'] = '数据为空'
                    break
                    
            except Exception as e:
                result['error'] = str(e)
                print(f"    {symbol} 更新失败: {e}")
                
                if attempt < self.config['retry_times'] - 1:
                    delay = self.config['retry_delay'] * (attempt + 1)
                    print(f"    {delay}秒后重试...")
                    time.sleep(delay)
                else:
                    print(f"    {symbol} 重试{self.config['retry_times']}次均失败")
        
        return result
    
    def update_stocks_batch(self, symbols: List[str], start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """批量更新股票数据"""
        results = []
        
        print(f"批量更新 {len(symbols)} 支股票 ({start_date} 至 {end_date})...")
        
        # 分批处理，避免内存过大
        for i in range(0, len(symbols), self.config['batch_size']):
            batch = symbols[i:i + self.config['batch_size']]
            batch_start = i + 1
            batch_end = min(i + len(batch), len(symbols))
            
            print(f"处理批次 {batch_start}-{batch_end}/{len(symbols)}...")
            
            batch_results = []
            for symbol in batch:
                result = self.update_single_stock(symbol, start_date, end_date)
                batch_results.append(result)
                
                # 短暂延迟，避免请求过快
                time.sleep(0.1)
            
            results.extend(batch_results)
            
            # 批次间稍长延迟
            if i + self.config['batch_size'] < len(symbols):
                time.sleep(1)
        
        return results
    
    def update_market_indices(self):
        """更新市场指数"""
        indices = [
            {'symbol': '000001', 'name': '上证指数'},
            {'symbol': '399001', 'name': '深证成指'},
            {'symbol': '399006', 'name': '创业板指'},
            {'symbol': '000300', 'name': '沪深300'},
            {'symbol': '000905', 'name': '中证500'}
        ]
        
        print(f"\n更新市场指数...")
        
        start_date, end_date, update_type = self.get_update_date_range()
        if start_date is None:
            print("  无需更新指数")
            return
        
        for idx in indices:
            try:
                print(f"  更新指数 {idx['symbol']} ({idx['name']})...")
                
                # 获取指数数据
                df = self.backfiller.fetch_index_data(
                    symbol=idx['symbol'],
                    name=idx['name'],
                    start_date=start_date,
                    end_date=end_date
                )
                
                if not df.empty:
                    new_count, update_count = self.db.insert_daily_prices(
                        idx['symbol'], df, data_source='index'
                    )
                    print(f"    指数{idx['symbol']}: 新增{new_count}条, 更新{update_count}条")
                else:
                    print(f"    指数{idx['symbol']}数据获取失败")
                    
            except Exception as e:
                print(f"    更新指数{idx['symbol']}失败: {e}")
    
    def update_market_status(self):
        """更新市场状态数据"""
        try:
            import akshare as ak
            
            print(f"\n更新市场状态数据...")
            
            # 获取当前日期
            today = datetime.now().strftime('%Y-%m-%d')
            
            # 检查是否已更新
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM market_status WHERE date = ?",
                    (today,)
                )
                if cursor.fetchone() is not None:
                    print(f"  今日市场状态已更新")
                    return
            
            # 获取市场概况
            try:
                # 获取上涨下跌家数
                spot_df = ak.stock_zh_a_spot_em()
                if spot_df is not None and not spot_df.empty:
                    # 计算统计
                    total = len(spot_df)
                    advancers = len(spot_df[spot_df['涨跌幅'] > 0])
                    decliners = len(spot_df[spot_df['涨跌幅'] < 0])
                    unchanged = total - advancers - decliners
                    limit_up = len(spot_df[spot_df['涨跌幅'] >= 9.8])  # 近似涨停
                    limit_down = len(spot_df[spot_df['涨跌幅'] <= -9.8])  # 近似跌停
                    
                    # 计算总成交额
                    total_amount = spot_df['成交额'].sum() if '成交额' in spot_df.columns else 0
                    total_volume = spot_df['成交量'].sum() if '成交量' in spot_df.columns else 0
                    
                    # 北向资金（简化）
                    try:
                        northbound = ak.stock_hsgt_north_net_flow_in_em()
                        if northbound is not None and not northbound.empty:
                            latest = northbound.iloc[-1]
                            north_inflow = latest.get('value', 0)
                        else:
                            north_inflow = 0
                    except:
                        north_inflow = 0
                    
                    # 构建市场状态数据
                    market_data = {
                        'date': today,
                        'total_market_cap': 0,  # 需要额外数据
                        'trading_volume': float(total_volume),
                        'trading_amount': float(total_amount),
                        'advancers': int(advancers),
                        'decliners': int(decliners),
                        'unchanged': int(unchanged),
                        'limit_up': int(limit_up),
                        'limit_down': int(limit_down),
                        'sh_pe': 0,  # 需要额外数据
                        'sz_pe': 0,
                        'gem_pe': 0,
                        'star_pe': 0,
                        'northbound_inflow': float(north_inflow),
                        'southbound_inflow': 0,
                        'margin_balance': 0,
                        'short_balance': 0
                    }
                    
                    # 保存到数据库
                    self.db.update_market_status(today, market_data)
                    print(f"  市场状态数据已更新")
                    
            except Exception as e:
                print(f"  获取市场状态失败: {e}")
                
        except ImportError:
            print(f"  AKShare不可用，跳过市场状态更新")
        except Exception as e:
            print(f"  更新市场状态失败: {e}")
    
    def check_data_consistency(self):
        """检查数据一致性"""
        print(f"\n检查数据一致性...")
        
        stats = self.db.get_update_stats(days=1)
        
        if stats['failed_updates'] > 0:
            print(f"  警告: 最近24小时有{stats['failed_updates']}次更新失败")
        
        # 检查缺失数据的股票
        symbols = self.db.get_all_stocks(status='active')
        missing_data = []
        
        for stock in symbols[:50]:  # 只检查前50支，避免耗时过长
            symbol = stock['symbol']
            last_date = self.db.get_last_trading_date(symbol)
            
            if last_date:
                last_dt = datetime.strptime(last_date, '%Y-%m-%d')
                days_diff = (datetime.now() - last_dt).days
                
                if days_diff > 10 and self.is_trading_day():
                    missing_data.append({
                        'symbol': symbol,
                        'last_date': last_date,
                        'days_missing': days_diff
                    })
        
        if missing_data:
            print(f"  警告: {len(missing_data)}支股票数据缺失超过10天")
            for item in missing_data[:5]:  # 只显示前5支
                print(f"    {item['symbol']}: 最后更新{item['last_date']}, 缺失{item['days_missing']}天")
        
        return len(missing_data)
    
    def run_daily_update(self, force: bool = False) -> Dict[str, Any]:
        """运行每日更新"""
        print("=" * 70)
        print("每日数据更新工具")
        print("=" * 70)
        
        start_time = time.time()
        today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"运行时间: {today}")
        print(f"强制更新: {force}")
        
        # 检查是否为交易日
        if not force and not self.is_trading_day():
            print("今日非交易日，跳过更新")
            return {
                'timestamp': today,
                'status': 'skipped',
                'reason': '非交易日',
                'duration_seconds': 0
            }
        
        # 获取更新日期范围
        start_date, end_date, update_type = self.get_update_date_range()
        
        if start_date is None:
            print("无需更新")
            return {
                'timestamp': today,
                'status': 'skipped',
                'reason': '无需更新',
                'duration_seconds': 0
            }
        
        print(f"更新类型: {update_type}")
        print(f"更新范围: {start_date} 至 {end_date}")
        
        # 获取需要更新的股票
        symbols = self.get_stocks_to_update()
        
        if not symbols:
            print("没有需要更新的股票")
            return {
                'timestamp': today,
                'status': 'failed',
                'reason': '无股票数据',
                'duration_seconds': time.time() - start_time
            }
        
        # 更新股票数据
        print(f"\n开始更新股票数据...")
        stock_results = self.update_stocks_batch(symbols, start_date, end_date)
        
        # 更新指数数据
        self.update_market_indices()
        
        # 更新市场状态
        self.update_market_status()
        
        # 统计结果
        success_count = sum(1 for r in stock_results if r['success'])
        fail_count = len(stock_results) - success_count
        total_new = sum(r['new_count'] for r in stock_results)
        total_updated = sum(r['update_count'] for r in stock_results)
        
        # 检查数据一致性
        missing_count = self.check_data_consistency()
        
        end_time = time.time()
        duration_seconds = end_time - start_time
        
        print("\n" + "=" * 70)
        print("每日更新完成!")
        print("=" * 70)
        print(f"股票更新: {success_count}成功, {fail_count}失败")
        print(f"价格数据: 新增{total_new}条, 更新{total_updated}条")
        print(f"数据缺失: {missing_count}支股票数据陈旧")
        print(f"总耗时: {duration_seconds:.1f}秒")
        
        # 构建结果报告
        report = {
            'timestamp': today,
            'status': 'success' if fail_count == 0 else 'partial',
            'update_type': update_type,
            'date_range': f"{start_date} 至 {end_date}",
            'duration_seconds': duration_seconds,
            'stocks_total': len(symbols),
            'stocks_success': success_count,
            'stocks_failed': fail_count,
            'price_data_new': total_new,
            'price_data_updated': total_updated,
            'data_missing_count': missing_count,
            'failed_symbols': [
                r['symbol'] for r in stock_results 
                if not r['success'] and r.get('error')
            ][:10]  # 只记录前10个失败
        }
        
        # 保存报告
        report_dir = os.path.join(os.path.dirname(self.db.db_path), "reports")
        os.makedirs(report_dir, exist_ok=True)
        
        report_file = os.path.join(report_dir, f"daily_update_{datetime.now().strftime('%Y%m%d')}.json")
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"更新报告已保存: {report_file}")
        
        # 显示数据库状态
        stats = self.db.get_update_stats()
        print(f"\n数据库状态:")
        print(f"  总日线记录: {stats['total_daily_records']:,}条")
        print(f"  活跃股票数: {stats['total_active_stocks']}支")
        print(f"  最近成功更新: {stats['success_updates']}次")
        print(f"  最近失败更新: {stats['failed_updates']}次")
        
        return report
    
    def create_systemd_service(self):
        """创建systemd服务文件（用于生产环境）"""
        service_content = f'''[Unit]
Description=Quant Historical Data Daily Updater
After=network.target

[Service]
Type=oneshot
User=root
WorkingDirectory={os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}
ExecStart=/usr/bin/python3 {os.path.abspath(__file__)}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
'''
        
        service_path = "/etc/systemd/system/quant-daily-update.service"
        
        print(f"Systemd服务文件内容:")
        print("-" * 50)
        print(service_content)
        print("-" * 50)
        print(f"保存到: {service_path}")
        
        try:
            with open(service_path, 'w') as f:
                f.write(service_content)
            print("服务文件创建成功")
            
            # 创建定时器
            timer_content = '''[Unit]
Description=Run quant daily update at 16:30 every weekday

[Timer]
OnCalendar=Mon..Fri 16:30:00
Persistent=true

[Install]
WantedBy=timers.target
'''
            
            timer_path = "/etc/systemd/system/quant-daily-update.timer"
            with open(timer_path, 'w') as f:
                f.write(timer_content)
            
            print(f"定时器文件创建成功: {timer_path}")
            print("\n启用服务:")
            print("sudo systemctl enable quant-daily-update.timer")
            print("sudo systemctl start quant-daily-update.timer")
            print("sudo systemctl status quant-daily-update.timer")
            
        except PermissionError:
            print("需要root权限创建systemd服务文件")
            print(f"请手动创建: {service_path}")
    
    def create_cron_job(self):
        """创建cron任务（备用）"""
        cron_time = "30 16 * * 1-5"  # 工作日16:30
        script_path = os.path.abspath(__file__)
        command = f"{cron_time} /usr/bin/python3 {script_path} >> /var/log/quant-daily-update.log 2>&1"
        
        print(f"Cron任务:")
        print("-" * 50)
        print(command)
        print("-" * 50)
        print("添加到crontab:")
        print(f"crontab -e")
        print(f"然后添加以上行")


# 命令行接口
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='每日数据更新工具')
    parser.add_argument('--force', action='store_true', help='强制更新（即使非交易日）')
    parser.add_argument('--workers', type=int, default=8, help='并发工作数')
    parser.add_argument('--create-service', action='store_true', help='创建systemd服务文件')
    parser.add_argument('--create-cron', action='store_true', help='创建cron任务')
    parser.add_argument('--test', action='store_true', help='测试模式（只更新少量股票）')
    
    args = parser.parse_args()
    
    # 创建更新器
    updater = DailyDataUpdater(max_workers=args.workers)
    
    if args.create_service:
        updater.create_systemd_service()
    elif args.create_cron:
        updater.create_cron_job()
    else:
        # 运行更新
        if args.test:
            # 测试模式：只更新少量股票
            test_symbols = updater.backfiller.default_symbols[:10]
            updater.db = DatabaseManager()  # 确保使用真实数据库
        
        report = updater.run_daily_update(force=args.force)
        
        # 根据结果返回适当退出码
        if report['status'] == 'success':
            sys.exit(0)
        elif report['status'] == 'partial':
            sys.exit(1)
        else:
            sys.exit(2)