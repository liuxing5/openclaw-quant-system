#!/usr/bin/env python3
"""
历史数据库初始化脚本
创建SQLite数据库，包含全A股2010年至今的历史数据表结构
"""

import sqlite3
import os
from datetime import datetime

# 数据库路径
DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, "quant_history.db")

def create_tables():
    """创建所有数据表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print(f"初始化数据库: {DB_PATH}")
    
    # 1. 股票基本信息表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS stocks (
        symbol TEXT PRIMARY KEY,          -- 股票代码 (600519)
        name TEXT,                        -- 股票名称
        market TEXT,                      -- 市场 (SH/SZ/BJ)
        listing_date TEXT,                -- 上市日期
        industry TEXT,                    -- 所属行业
        sub_industry TEXT,                -- 细分行业
        status TEXT DEFAULT 'active',     -- 状态 (active/delisted/suspended)
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # 2. 日线价格表（主表）
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS daily_prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        date DATE NOT NULL,
        open REAL,                        -- 开盘价
        high REAL,                        -- 最高价
        low REAL,                         -- 最低价
        close REAL,                       -- 收盘价
        volume REAL,                      -- 成交量(股)
        amount REAL,                      -- 成交额(元)
        change REAL,                      -- 涨跌额
        change_pct REAL,                  -- 涨跌幅(%)
        turnover REAL,                    -- 换手率(%)
        amplitude REAL,                   -- 振幅(%)
        pre_close REAL,                   -- 前收盘价
        adj_factor REAL DEFAULT 1.0,      -- 复权因子
        adj_close REAL,                   -- 复权收盘价
        data_source TEXT DEFAULT 'akshare', -- 数据源
        quality_score REAL DEFAULT 1.0,   -- 数据质量评分
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, date)
    )
    ''')
    
    # 3. 日线价格索引（加速查询）
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_daily_symbol ON daily_prices(symbol)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_prices(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_daily_symbol_date ON daily_prices(symbol, date)')
    
    # 4. 分钟价格表（可选，按需创建）
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS minute_prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        datetime TIMESTAMP NOT NULL,      -- 日期时间
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume REAL,
        amount REAL,
        frequency TEXT DEFAULT '1min',    -- 频率 (1min/5min/15min/30min/60min)
        data_source TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, datetime, frequency)
    )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_minute_symbol ON minute_prices(symbol)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_minute_datetime ON minute_prices(datetime)')
    
    # 5. 财务数据表（季度）
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS financials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        report_date TEXT NOT NULL,        -- 报告期 (YYYY-03-31等)
        report_type TEXT,                 -- 报告类型 (Q1/Q2/Q3/Annual)
        eps REAL,                         -- 每股收益
        eps_yoy REAL,                     -- 每股收益同比%
        revenue REAL,                     -- 营业收入
        revenue_yoy REAL,                 -- 营收同比%
        net_profit REAL,                  -- 净利润
        net_profit_yoy REAL,              -- 净利润同比%
        roe REAL,                         -- 净资产收益率%
        roa REAL,                         -- 总资产收益率%
        gross_margin REAL,                -- 毛利率%
        net_margin REAL,                  -- 净利率%
        debt_ratio REAL,                  -- 资产负债率%
        current_ratio REAL,               -- 流动比率
        bps REAL,                         -- 每股净资产
        cash_flow_operating REAL,         -- 经营现金流
        pe REAL,                          -- 市盈率
        pb REAL,                          -- 市净率
        data_source TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, report_date)
    )
    ''')
    
    # 6. 指数数据表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS index_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,             -- 指数代码 (000001.SH等)
        name TEXT,                        -- 指数名称
        date DATE NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume REAL,
        amount REAL,
        change REAL,
        change_pct REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, date)
    )
    ''')
    
    # 7. 数据更新日志表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS update_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_type TEXT NOT NULL,          -- 任务类型 (daily_update/backfill/cleanup)
        symbol TEXT,                      -- 股票代码 (ALL表示全市场)
        date_range TEXT,                  -- 日期范围
        records_updated INTEGER,          -- 更新记录数
        records_new INTEGER,              -- 新增记录数
        start_time TIMESTAMP,             -- 开始时间
        end_time TIMESTAMP,               -- 结束时间
        status TEXT,                      -- 状态 (success/failed/partial)
        error_message TEXT,               -- 错误信息
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # 8. 市场状态表（情绪、资金流等）
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS market_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date DATE NOT NULL,
        total_market_cap REAL,            -- 总市值
        trading_volume REAL,              -- 总成交量
        trading_amount REAL,              -- 总成交额
        advancers INTEGER,                -- 上涨家数
        decliners INTEGER,                -- 下跌家数
        unchanged INTEGER,                -- 平盘家数
        limit_up INTEGER,                 -- 涨停家数
        limit_down INTEGER,               -- 跌停家数
        sh_pe REAL,                       -- 上证PE
        sz_pe REAL,                       -- 深证PE
        gem_pe REAL,                      -- 创业板PE
        star_pe REAL,                     -- 科创板PE
        northbound_inflow REAL,           -- 北向资金净流入
        southbound_inflow REAL,           -- 南向资金净流入
        margin_balance REAL,              -- 融资余额
        short_balance REAL,               -- 融券余额
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(date)
    )
    ''')
    
    # 提交更改
    conn.commit()
    
    # 检查表创建情况
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    
    print(f"创建了 {len(tables)} 个数据表:")
    for table in tables:
        print(f"  - {table[0]}")
    
    conn.close()
    print("数据库初始化完成!")
    return True

def check_database_size():
    """检查数据库大小"""
    if os.path.exists(DB_PATH):
        size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
        print(f"数据库大小: {size_mb:.2f} MB")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 检查各表记录数
        tables = ['stocks', 'daily_prices', 'financials', 'index_data', 'market_status']
        for table in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                print(f"  {table}: {count:,} 条记录")
            except:
                pass
        
        conn.close()
    else:
        print("数据库文件不存在")

def backup_database():
    """备份数据库"""
    if os.path.exists(DB_PATH):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = DB_PATH.replace('.db', f'_backup_{timestamp}.db')
        
        import shutil
        shutil.copy2(DB_PATH, backup_path)
        print(f"数据库备份已创建: {backup_path}")
        return backup_path
    return None

if __name__ == "__main__":
    print("=" * 60)
    print("量化历史数据库初始化工具")
    print("=" * 60)
    
    # 备份现有数据库（如果存在）
    if os.path.exists(DB_PATH):
        print("发现现有数据库，创建备份...")
        backup_database()
    
    # 创建新数据库
    create_tables()
    
    # 检查数据库状态
    check_database_size()
    
    print("\n数据库结构说明:")
    print("1. stocks: 股票基本信息表")
    print("2. daily_prices: 日线价格表（核心数据）")
    print("3. minute_prices: 分钟价格表（按需使用）")
    print("4. financials: 财务数据表（季度/年度）")
    print("5. index_data: 指数数据表")
    print("6. market_status: 市场状态表")
    print("7. update_logs: 数据更新日志表")
    
    print("\n下一步操作:")
    print("1. 运行 backfill_all_stocks.py 批量填充历史数据")
    print("2. 运行 daily_update.py 设置每日自动更新")
    print("3. 运行 test_database.py 测试数据库功能")