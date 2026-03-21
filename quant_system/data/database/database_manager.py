#!/usr/bin/env python3
"""
历史数据库管理器
提供对SQLite数据库的CRUD操作和高级查询接口
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import os
import json
import time
from contextlib import contextmanager

class DatabaseManager:
    """历史数据库管理器"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(db_dir, "quant_history.db")
        
        self.db_path = db_path
        self._ensure_db_exists()
    
    def _ensure_db_exists(self):
        """确保数据库文件存在，如果不存在则初始化"""
        if not os.path.exists(self.db_path):
            print(f"数据库文件不存在，正在初始化: {self.db_path}")
            from init_database import create_tables
            create_tables()
        elif os.path.getsize(self.db_path) == 0:
            print(f"数据库文件为空，重新初始化: {self.db_path}")
            from init_database import create_tables
            create_tables()
    
    @contextmanager
    def get_connection(self, timeout: int = 30, max_retries: int = 5):
        """获取数据库连接（带重试的上下文管理器）
        
        Args:
            timeout: 连接超时时间（秒）
            max_retries: 最大重试次数
        """
        retry_count = 0
        retry_delay = 0.1  # 初始延迟100ms
        
        while retry_count < max_retries:
            try:
                conn = sqlite3.connect(
                    self.db_path,
                    timeout=timeout,
                    isolation_level=None  # 自动提交模式，减少锁争用
                )
                conn.row_factory = sqlite3.Row  # 允许通过列名访问
                conn.execute("PRAGMA journal_mode=WAL")  # WAL模式，支持读写并发
                conn.execute("PRAGMA synchronous=NORMAL")  # 平衡性能和数据安全
                conn.execute("PRAGMA cache_size=-2000")  # 2MB缓存
                
                try:
                    yield conn
                    # 在WAL模式下，commit是轻量级的
                    conn.commit()
                    break  # 成功，退出重试循环
                    
                except sqlite3.OperationalError as e:
                    conn.rollback()
                    conn.close()
                    
                    if "database is locked" in str(e) and retry_count < max_retries - 1:
                        retry_count += 1
                        sleep_time = retry_delay * (2 ** retry_count)  # 指数退避
                        sleep_time = min(sleep_time, 5.0)  # 最大5秒
                        print(f"数据库锁定，第{retry_count}次重试，等待{sleep_time:.1f}秒...")
                        time.sleep(sleep_time)
                        continue
                    else:
                        raise e
                        
                except Exception as e:
                    conn.rollback()
                    raise e
                    
                finally:
                    conn.close()
                    
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and retry_count < max_retries - 1:
                    retry_count += 1
                    sleep_time = retry_delay * (2 ** retry_count)
                    sleep_time = min(sleep_time, 5.0)
                    print(f"数据库连接锁定，第{retry_count}次重试，等待{sleep_time:.1f}秒...")
                    time.sleep(sleep_time)
                    continue
                else:
                    raise e
    
    def get_connection_simple(self):
        """简单连接（兼容旧代码）"""
        return self.get_connection()
    
    # ========== 股票基本信息操作 ==========
    
    def upsert_stock(self, symbol: str, name: str = None, market: str = None,
                    listing_date: str = None, industry: str = None, 
                    sub_industry: str = None, status: str = 'active') -> bool:
        """插入或更新股票基本信息"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 检查是否已存在
            cursor.execute("SELECT symbol FROM stocks WHERE symbol = ?", (symbol,))
            exists = cursor.fetchone() is not None
            
            if exists:
                # 更新
                query = """
                UPDATE stocks SET 
                    name = COALESCE(?, name),
                    market = COALESCE(?, market),
                    listing_date = COALESCE(?, listing_date),
                    industry = COALESCE(?, industry),
                    sub_industry = COALESCE(?, sub_industry),
                    status = COALESCE(?, status),
                    updated_at = CURRENT_TIMESTAMP
                WHERE symbol = ?
                """
                cursor.execute(query, (name, market, listing_date, industry, 
                                     sub_industry, status, symbol))
                return cursor.rowcount > 0
            else:
                # 插入
                query = """
                INSERT INTO stocks 
                (symbol, name, market, listing_date, industry, sub_industry, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """
                cursor.execute(query, (symbol, name, market, listing_date, 
                                     industry, sub_industry, status))
                return cursor.rowcount > 0
    
    def get_stock_info(self, symbol: str) -> Optional[Dict]:
        """获取股票基本信息"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stocks WHERE symbol = ?", (symbol,))
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            return None
    
    def get_all_stocks(self, status: str = 'active') -> List[Dict]:
        """获取所有股票列表"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stocks WHERE status = ? ORDER BY symbol", (status,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_stocks_by_industry(self, industry: str) -> List[Dict]:
        """按行业获取股票列表"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stocks WHERE industry = ? AND status = 'active' ORDER BY symbol", (industry,))
            return [dict(row) for row in cursor.fetchall()]
    
    # ========== 日线价格操作 ==========
    
    def insert_daily_prices(self, symbol: str, df: pd.DataFrame, 
                           data_source: str = 'akshare') -> Tuple[int, int]:
        """
        插入日线价格数据
        返回: (新增记录数, 更新记录数)
        """
        # 🚨 关键修复：防止模拟数据污染数据库，确保系统信任链完整
        # 用户指出问题：模拟数据写入数据库后，后续调用从"本地数据库"取出，
        # 元数据显示"来源：本地数据库"，完全看不出来是模拟的
        if data_source == 'simulated':
            raise ValueError(
                f"禁止将模拟数据写入数据库（symbol: {symbol}）: "
                "模拟数据会污染数据库，导致系统信任链断裂。"
                "模拟数据仅用于测试和开发，不应写入生产数据库。"
            )
        
        # 🚨 关键修复：防止腾讯财经假数据污染数据库，确保因子计算准确性
        # 用户指出问题：腾讯财经的OHLC是按固定比例缩放的假数据
        # open = close * 0.99，high = close * 1.02，volume = 1000000（常数）
        # 这批数据被写入数据库后，所有依赖OHLC的因子（ATR、布林带、振幅、成交量突破）的计算结果全部是错的
        if data_source == 'tencent':
            raise ValueError(
                f"禁止将腾讯财经假数据写入数据库（symbol: {symbol}）: "
                "腾讯财经数据源提供的是按固定比例缩放的假OHLC数据，"
                "会导致所有技术因子计算错误。"
                "请使用Baostock或AKShare等可靠数据源。"
            )
        
        if df.empty:
            return 0, 0
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            new_count = 0
            update_count = 0
            
            for date_str, row in df.iterrows():
                # 确保日期格式
                if hasattr(date_str, 'strftime'):
                    date = date_str.strftime('%Y-%m-%d')
                else:
                    date = str(date_str)[:10]
                
                # 检查是否已存在
                cursor.execute(
                    "SELECT id FROM daily_prices WHERE symbol = ? AND date = ?",
                    (symbol, date)
                )
                exists = cursor.fetchone() is not None
                
                # 计算复权收盘价（如果有复权因子）
                adj_close = row.get('close', 0)
                if 'adj_factor' in row and row['adj_factor'] != 1.0:
                    adj_close = row['close'] * row['adj_factor']
                
                if exists:
                    # 更新
                    query = """
                    UPDATE daily_prices SET 
                        open = ?, high = ?, low = ?, close = ?,
                        volume = ?, amount = ?, change = ?, change_pct = ?,
                        turnover = ?, amplitude = ?, pre_close = ?,
                        adj_factor = ?, adj_close = ?, data_source = ?,
                        quality_score = ?
                    WHERE symbol = ? AND date = ?
                    """
                    cursor.execute(query, (
                        row.get('open'), row.get('high'), row.get('low'), 
                        row.get('close'), row.get('volume'), row.get('amount'),
                        row.get('change'), row.get('change_pct'), 
                        row.get('turnover'), row.get('amplitude'),
                        row.get('pre_close'), row.get('adj_factor', 1.0),
                        adj_close, data_source, row.get('quality_score', 1.0),
                        symbol, date
                    ))
                    if cursor.rowcount > 0:
                        update_count += 1
                else:
                    # 插入
                    query = """
                    INSERT INTO daily_prices 
                    (symbol, date, open, high, low, close, volume, amount, 
                     change, change_pct, turnover, amplitude, pre_close,
                     adj_factor, adj_close, data_source, quality_score)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                    cursor.execute(query, (
                        symbol, date, row.get('open'), row.get('high'), 
                        row.get('low'), row.get('close'), row.get('volume'),
                        row.get('amount'), row.get('change'), row.get('change_pct'),
                        row.get('turnover'), row.get('amplitude'), 
                        row.get('pre_close'), row.get('adj_factor', 1.0),
                        adj_close, data_source, row.get('quality_score', 1.0)
                    ))
                    if cursor.rowcount > 0:
                        new_count += 1
            
            # 记录更新日志
            if new_count > 0 or update_count > 0:
                self._log_update('daily_update', symbol, new_count + update_count, 
                               new_count, 'success')
            
            return new_count, update_count
    
    def get_daily_prices(self, symbol: str, start_date: str = None, 
                        end_date: str = None, limit: int = None) -> pd.DataFrame:
        """
        获取日线价格数据
        """
        with self.get_connection() as conn:
            query = "SELECT * FROM daily_prices WHERE symbol = ?"
            params = [symbol]
            
            if start_date:
                query += " AND date >= ?"
                params.append(start_date)
            
            if end_date:
                query += " AND date <= ?"
                params.append(end_date)
            
            query += " ORDER BY date"
            
            if limit:
                query += " LIMIT ?"
                params.append(limit)
            
            df = pd.read_sql_query(query, conn, params=params)
            
            if not df.empty:
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                df.sort_index(inplace=True)
            
            return df
    
    def get_last_trading_date(self, symbol: str = None) -> Optional[str]:
        """获取最后交易日期（全市场或指定股票）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if symbol:
                cursor.execute(
                    "SELECT MAX(date) FROM daily_prices WHERE symbol = ?",
                    (symbol,)
                )
            else:
                cursor.execute("SELECT MAX(date) FROM daily_prices")
            
            result = cursor.fetchone()
            return result[0] if result and result[0] else None
    
    def get_price_range(self, symbol: str) -> Tuple[str, str]:
        """获取股票价格数据的时间范围"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT MIN(date), MAX(date) FROM daily_prices WHERE symbol = ?",
                (symbol,)
            )
            result = cursor.fetchone()
            
            if result and result[0] and result[1]:
                return result[0], result[1]
            return None, None
    
    # ========== 财务数据操作 ==========
    
    def insert_financials(self, symbol: str, df: pd.DataFrame, 
                         data_source: str = 'tushare') -> int:
        """
        插入财务数据
        返回: 插入记录数
        """
        if df.empty:
            return 0
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            count = 0
            
            for _, row in df.iterrows():
                # 检查是否已存在
                cursor.execute(
                    "SELECT id FROM financials WHERE symbol = ? AND report_date = ?",
                    (symbol, row.get('report_date'))
                )
                exists = cursor.fetchone() is not None
                
                if not exists:
                    query = """
                    INSERT INTO financials 
                    (symbol, report_date, report_type, eps, eps_yoy, revenue, 
                     revenue_yoy, net_profit, net_profit_yoy, roe, roa, 
                     gross_margin, net_margin, debt_ratio, current_ratio, 
                     bps, cash_flow_operating, pe, pb, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                    cursor.execute(query, (
                        symbol, row.get('report_date'), row.get('report_type'),
                        row.get('eps'), row.get('eps_yoy'), row.get('revenue'),
                        row.get('revenue_yoy'), row.get('net_profit'),
                        row.get('net_profit_yoy'), row.get('roe'), row.get('roa'),
                        row.get('gross_margin'), row.get('net_margin'),
                        row.get('debt_ratio'), row.get('current_ratio'),
                        row.get('bps'), row.get('cash_flow_operating'),
                        row.get('pe'), row.get('pb'), data_source
                    ))
                    
                    if cursor.rowcount > 0:
                        count += 1
            
            if count > 0:
                self._log_update('financial_update', symbol, count, count, 'success')
            
            return count
    
    def get_latest_financials(self, symbol: str) -> Optional[Dict]:
        """获取最新财务数据"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM financials 
                WHERE symbol = ? 
                ORDER BY report_date DESC 
                LIMIT 1
            """, (symbol,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_financial_history(self, symbol: str, report_type: str = None) -> pd.DataFrame:
        """获取财务历史数据"""
        with self.get_connection() as conn:
            query = "SELECT * FROM financials WHERE symbol = ?"
            params = [symbol]
            
            if report_type:
                query += " AND report_type = ?"
                params.append(report_type)
            
            query += " ORDER BY report_date"
            
            df = pd.read_sql_query(query, conn, params=params)
            return df
    
    # ========== 指数数据操作 ==========
    
    def insert_index_data(self, symbol: str, df: pd.DataFrame) -> int:
        """插入指数数据"""
        if df.empty:
            return 0
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            count = 0
            
            for date_str, row in df.iterrows():
                if hasattr(date_str, 'strftime'):
                    date = date_str.strftime('%Y-%m-%d')
                else:
                    date = str(date_str)[:10]
                
                # 检查是否已存在
                cursor.execute(
                    "SELECT id FROM index_data WHERE symbol = ? AND date = ?",
                    (symbol, date)
                )
                exists = cursor.fetchone() is not None
                
                if not exists:
                    query = """
                    INSERT INTO index_data 
                    (symbol, name, date, open, high, low, close, volume, amount, change, change_pct)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                    cursor.execute(query, (
                        symbol, row.get('name', ''), date,
                        row.get('open'), row.get('high'), row.get('low'), 
                        row.get('close'), row.get('volume'), row.get('amount'),
                        row.get('change'), row.get('change_pct')
                    ))
                    
                    if cursor.rowcount > 0:
                        count += 1
            
            return count
    
    # ========== 市场状态操作 ==========
    
    def update_market_status(self, date: str, data: Dict) -> bool:
        """更新市场状态数据"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 检查是否已存在
            cursor.execute("SELECT id FROM market_status WHERE date = ?", (date,))
            exists = cursor.fetchone() is not None
            
            if exists:
                # 构建更新语句
                set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
                values = list(data.values()) + [date]
                
                query = f"UPDATE market_status SET {set_clause} WHERE date = ?"
                cursor.execute(query, values)
            else:
                # 插入
                columns = ["date"] + list(data.keys())
                placeholders = ["?"] * len(columns)
                
                query = f"""
                INSERT INTO market_status ({", ".join(columns)})
                VALUES ({", ".join(placeholders)})
                """
                cursor.execute(query, [date] + list(data.values()))
            
            return cursor.rowcount > 0
    
    # ========== 数据质量与维护 ==========
    
    def _log_update(self, task_type: str, symbol: str, records_updated: int,
                   records_new: int, status: str, error_message: str = None):
        """记录数据更新日志"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                query = """
                INSERT INTO update_logs 
                (task_type, symbol, date_range, records_updated, records_new, 
                 start_time, end_time, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
                """
                
                cursor.execute(query, (
                    task_type, symbol, "", records_updated, records_new,
                    start_time, status, error_message
                ))
        except Exception as e:
            # 日志记录失败不应该影响主流程
            print(f"警告: 记录更新日志失败: {e}")
    
    def get_update_stats(self, days: int = 7) -> Dict:
        """获取最近更新统计"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 最近成功更新
            cursor.execute("""
                SELECT COUNT(*) as success_count
                FROM update_logs 
                WHERE status = 'success' 
                AND datetime(start_time) > datetime('now', ?)
            """, (f'-{days} days',))
            success_count = cursor.fetchone()[0]
            
            # 最近失败更新
            cursor.execute("""
                SELECT COUNT(*) as failed_count
                FROM update_logs 
                WHERE status = 'failed' 
                AND datetime(start_time) > datetime('now', ?)
            """, (f'-{days} days',))
            failed_count = cursor.fetchone()[0]
            
            # 总数据量
            cursor.execute("SELECT COUNT(*) FROM daily_prices")
            total_daily = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM stocks WHERE status = 'active'")
            total_stocks = cursor.fetchone()[0]
            
            return {
                'success_updates': success_count,
                'failed_updates': failed_count,
                'total_daily_records': total_daily,
                'total_active_stocks': total_stocks,
                'database_size_mb': os.path.getsize(self.db_path) / (1024 * 1024)
            }
    
    def check_data_quality(self, symbol: str = None) -> Dict:
        """检查数据质量"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            results = {}
            
            if symbol:
                # 检查指定股票
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total_records,
                        MIN(date) as start_date,
                        MAX(date) as end_date,
                        AVG(quality_score) as avg_quality,
                        SUM(CASE WHEN open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL THEN 1 ELSE 0 END) as null_count
                    FROM daily_prices 
                    WHERE symbol = ?
                """, (symbol,))
                
                row = cursor.fetchone()
                if row:
                    results[symbol] = {
                        'total_records': row[0],
                        'date_range': f"{row[1]} 至 {row[2]}",
                        'avg_quality': round(row[3], 3),
                        'null_percentage': round(row[4] / max(1, row[0]) * 100, 2)
                    }
            else:
                # 检查全市场
                cursor.execute("""
                    SELECT 
                        COUNT(DISTINCT symbol) as stock_count,
                        COUNT(*) as total_records,
                        MIN(date) as earliest_date,
                        MAX(date) as latest_date
                    FROM daily_prices
                """)
                
                row = cursor.fetchone()
                results['overall'] = {
                    'stock_count': row[0],
                    'total_records': row[1],
                    'date_range': f"{row[2]} 至 {row[3]}",
                    'records_per_stock': round(row[1] / max(1, row[0]), 1)
                }
            
            return results
    
    def cleanup_old_data(self, keep_days: int = 365 * 10) -> int:
        """清理旧数据（保留10年）"""
        cutoff_date = (datetime.now() - timedelta(days=keep_days)).strftime('%Y-%m-%d')
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 删除旧日线数据
            cursor.execute(
                "DELETE FROM daily_prices WHERE date < ?",
                (cutoff_date,)
            )
            deleted_count = cursor.rowcount
            
            # 记录清理操作
            if deleted_count > 0:
                self._log_update('cleanup', 'ALL', 0, 0, 'success', 
                               f"清理{deleted_count}条{cutoff_date}前的数据")
            
            return deleted_count
    
    # ========== 高级查询接口 ==========
    
    def get_price_matrix(self, symbols: List[str], start_date: str, 
                        end_date: str, field: str = 'close') -> pd.DataFrame:
        """获取多只股票的价格矩阵（用于相关性分析等）"""
        with self.get_connection() as conn:
            # 为每只股票获取数据
            all_data = {}
            for symbol in symbols:
                df = self.get_daily_prices(symbol, start_date, end_date)
                if not df.empty and field in df.columns:
                    all_data[symbol] = df[field]
            
            # 合并为DataFrame
            if all_data:
                result = pd.DataFrame(all_data)
                result.index = pd.to_datetime(result.index)
                return result
            return pd.DataFrame()
    
    def get_returns(self, symbol: str, start_date: str, end_date: str, 
                   period: str = 'daily') -> pd.Series:
        """获取收益率序列"""
        df = self.get_daily_prices(symbol, start_date, end_date)
        
        if df.empty or 'close' not in df.columns:
            return pd.Series()
        
        if period == 'daily':
            returns = df['close'].pct_change()
        elif period == 'weekly':
            # 重采样为周收益率
            weekly_prices = df['close'].resample('W').last()
            returns = weekly_prices.pct_change()
        elif period == 'monthly':
            monthly_prices = df['close'].resample('ME').last()
            returns = monthly_prices.pct_change()
        else:
            returns = df['close'].pct_change()
        
        returns.name = f"{symbol}_returns"
        return returns.dropna()
    
    def get_volatility(self, symbol: str, window: int = 20, 
                      annualized: bool = True) -> pd.Series:
        """计算波动率"""
        df = self.get_daily_prices(symbol, limit=window * 5)  # 获取足够数据
        
        if df.empty or 'close' not in df.columns:
            return pd.Series()
        
        returns = df['close'].pct_change()
        volatility = returns.rolling(window).std()
        
        if annualized:
            volatility = volatility * np.sqrt(252)
        
        return volatility
    
    def export_to_csv(self, symbol: str, output_dir: str = None) -> str:
        """导出股票数据到CSV"""
        if output_dir is None:
            output_dir = os.path.dirname(self.db_path)
        
        os.makedirs(output_dir, exist_ok=True)
        
        # 获取所有数据
        df_daily = self.get_daily_prices(symbol)
        df_financial = self.get_financial_history(symbol)
        
        # 导出
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        if not df_daily.empty:
            daily_file = os.path.join(output_dir, f"{symbol}_daily_{timestamp}.csv")
            df_daily.to_csv(daily_file)
            print(f"日线数据已导出: {daily_file}")
        
        if not df_financial.empty:
            financial_file = os.path.join(output_dir, f"{symbol}_financial_{timestamp}.csv")
            df_financial.to_csv(financial_file)
            print(f"财务数据已导出: {financial_file}")
        
        return output_dir


# 测试
if __name__ == "__main__":
    print("测试数据库管理器...")
    
    # 创建管理器实例
    db = DatabaseManager()
    
    # 检查数据库状态
    stats = db.get_update_stats()
    print(f"数据库状态: {stats}")
    
    # 检查数据质量
    quality = db.check_data_quality()
    print(f"数据质量: {quality}")
    
    # 测试股票操作
    test_symbol = '600519'
    db.upsert_stock(
        symbol=test_symbol,
        name='贵州茅台',
        market='SH',
        listing_date='2001-08-27',
        industry='食品饮料',
        sub_industry='白酒'
    )
    
    stock_info = db.get_stock_info(test_symbol)
    print(f"股票信息: {stock_info}")
    
    # 测试价格数据操作（模拟数据）
    dates = pd.date_range(start='2025-01-01', end='2025-01-10', freq='D')
    test_df = pd.DataFrame({
        'open': np.random.normal(1600, 50, len(dates)),
        'high': np.random.normal(1650, 50, len(dates)),
        'low': np.random.normal(1550, 50, len(dates)),
        'close': np.random.normal(1620, 50, len(dates)),
        'volume': np.random.randint(1000000, 10000000, len(dates)),
        'amount': np.random.normal(1e9, 1e8, len(dates))
    }, index=dates)
    
    new_count, update_count = db.insert_daily_prices(test_symbol, test_df)
    print(f"插入日线数据: 新增{new_count}条, 更新{update_count}条")
    
    # 测试查询
    prices = db.get_daily_prices(test_symbol, '2025-01-01', '2025-01-10')
    print(f"查询结果: {len(prices)}条记录")
    
    last_date = db.get_last_trading_date(test_symbol)
    print(f"最后交易日期: {last_date}")
    
    print("数据库管理器测试完成!")