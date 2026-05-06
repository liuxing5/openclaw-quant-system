# -*- coding: utf-8 -*-
"""
================================================================================
短线打板策略 - 摇头哥战法增强版 V2.0 (AKShare+Baostock混合数据源)
================================================================================
作者: openclaw-quant-system 子模块
定位: 与"隔夜施工法"并行的短线打板策略
模式: 盘后筛选次日候选池
数据源策略:
    - AKShare: 涨停板池/龙虎榜/封单/炸板池 (独有数据,无替代)
    - Baostock: 历史日K线/5分钟K线/股票列表 (更稳定,无403风险)

数据源分工:
┌────────────────────┬──────────────┬──────────────┐
│ 数据类型           │ 主数据源     │ 备用数据源   │
├────────────────────┼──────────────┼──────────────┤
│ 涨停板池           │ AKShare      │ K线推算      │
│ 龙虎榜             │ AKShare      │ 无           │
│ 炸板池             │ AKShare      │ 无           │
│ 全市场快照         │ AKShare      │ Baostock循环 │
│ 5分钟K线(9:50反包) │ Baostock     │ AKShare      │
│ 历史日K线          │ Baostock     │ AKShare      │
│ 股票基础信息       │ Baostock     │ AKShare      │
└────────────────────┴──────────────┴──────────────┘

风控 (激进版):
    - 单票止损: 3%
    - 单票最大仓位: 20%
    - 总仓位上限: 50%

输出: ./reports/dabang_候选池_YYYYMMDD.{csv,md}
================================================================================
"""

import os
import sys
import time
import warnings
import logging
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')


# ============================================================================
# 全局配置
# ============================================================================
class Config:
    # 风控参数 (激进版)
    MAX_LOSS_PER_TRADE = 0.03
    MAX_POSITION_PER_STOCK = 0.20
    MAX_TOTAL_POSITION = 0.50
    
    # 强势期判断
    TOP_N_BY_AMOUNT = 5
    STRONG_THRESHOLD = 3
    HEAVY_THRESHOLD = 4
    
    # 选股方向
    HOT_AMOUNT_THRESHOLD = 10e8
    RECENT_2BAN_DAYS = 10
    RECENT_4BAN_DAYS = 90
    
    # 涨停判定阈值 (用于K线推算备用)
    ZT_PCT_THRESHOLD = 9.7         # 主板涨停
    ZT_PCT_THRESHOLD_CYB = 19.7    # 创业板/科创板
    
    # 准涨停池参数 (涨幅7%-9.5% + 量比>2)
    NEAR_ZT_PCT_LO = 7.0
    NEAR_ZT_PCT_HI = 9.5
    NEAR_ZT_VOL_RATIO_MIN = 2.0
    NEAR_ZT_WEIGHT = 0.8           # 准涨停权重打8折
    
    # 情绪指标参数
    DT_LIMIT_COUNT = 5             # 跌停家数>5说明恐慌
    ZT_PREMIUM_LOOKBACK = 1        # 昨日涨停今日溢价
    
    # 仓位管理参数
    MIN_AMOUNT_LIQUIDITY = 5e8     # 成交额<5亿流动性折扣
    LIQUIDITY_DISCOUNT = 0.5       # 流动性折扣系数
    MAX_PER_SECTOR = 2             # 同板块最多2只
    HIGH_BOARD_THRESHOLD = 5       # 5板以上仓位减半
    HIGH_BOARD_DISCOUNT = 0.5      # 高位板折扣
    
    # 因子参数
    LHB_LOOKBACK_DAYS = 5
    SEAL_RATIO_MIN = 0.005
    BLOCKUP_RATE_MAX = 0.30
    
    # 候选池
    MAX_CANDIDATES = 10
    MIN_TOTAL_SCORE = 60
    
    # 数据源开关 (出问题可手动切换)
    USE_AKSHARE = True              # 是否启用AKShare
    USE_BAOSTOCK = True             # 是否启用Baostock
    PREFER_BAOSTOCK_FOR_KLINE = True  # K线优先用Baostock
    
    # 路径 (本地化到 recommend/daban/ 目录)
    BASE_DIR = Path(__file__).resolve().parent
    OUTPUT_DIR = BASE_DIR / "reports"
    CACHE_DIR = BASE_DIR / "cache"
    
    # 网络重试
    MAX_RETRIES = 3
    RETRY_SLEEP = 1.0


# ============================================================================
# 日志
# ============================================================================
def setup_logger():
    Config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    Config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    log_file = Config.OUTPUT_DIR / f"dabang_{datetime.now().strftime('%Y%m%d')}.log"
    
    logger = logging.getLogger('dabang')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

logger = setup_logger()


# ============================================================================
# 工具函数: 重试装饰器
# ============================================================================
def safe_call(func, *args, max_retries=None, sleep_sec=None, **kwargs):
    """通用重试包装"""
    max_retries = max_retries or Config.MAX_RETRIES
    sleep_sec = sleep_sec or Config.RETRY_SLEEP
    
    for i in range(max_retries):
        try:
            result = func(*args, **kwargs)
            if result is None or (isinstance(result, pd.DataFrame) and result.empty):
                if i < max_retries - 1:
                    time.sleep(sleep_sec)
                    continue
            return result
        except Exception as e:
            if i < max_retries - 1:
                logger.warning(f"调用失败 [{func.__name__}] 重试{i+1}/{max_retries}: {str(e)[:100]}")
                time.sleep(sleep_sec * (i + 1))
            else:
                logger.error(f"调用彻底失败 [{func.__name__}]: {str(e)[:200]}")
                return None
    return None


# ============================================================================
# Baostock 连接管理器 (上下文管理,自动登录登出)
# ============================================================================
class BaostockSession:
    """Baostock会话单例,避免重复登录"""
    _instance = None
    _logged_in = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            try:
                import baostock as bs
                cls.bs = bs
                logger.info(f"Baostock 模块加载成功")
            except ImportError:
                logger.error("Baostock未安装: pip install baostock --break-system-packages")
                cls.bs = None
        return cls._instance
    
    def login(self):
        if self.bs is None:
            return False
        if self._logged_in:
            return True
        try:
            lg = self.bs.login()
            if lg.error_code == '0':
                self._logged_in = True
                logger.info("Baostock 登录成功")
                return True
            else:
                logger.error(f"Baostock 登录失败: {lg.error_code} - {lg.error_msg}")
                return False
        except Exception as e:
            logger.error(f"Baostock 登录异常: {e}")
            return False
    
    def logout(self):
        if self.bs and self._logged_in:
            try:
                self.bs.logout()
                self._logged_in = False
            except:
                pass
    
    @contextmanager
    def session(self):
        """上下文管理器: 自动登录,使用完保持连接"""
        ok = self.login()
        if not ok:
            raise RuntimeError("Baostock登录失败")
        try:
            yield self.bs
        except Exception as e:
            logger.error(f"Baostock会话内异常: {e}")
            raise


# ============================================================================
# 数据源1: Baostock (K线数据)
# ============================================================================
class BaostockProvider:
    """Baostock数据提供者 - 负责K线/5分钟/股票列表"""
    
    def __init__(self):
        self.session = BaostockSession()
        self._stock_list_cache = None
    
    @staticmethod
    def code_to_baostock(code):
        """代码格式转换: 600000 -> sh.600000, 000001 -> sz.000001"""
        code = str(code).zfill(6)
        if code.startswith(('60', '68', '90')):
            return f'sh.{code}'
        elif code.startswith(('00', '30', '20')):
            return f'sz.{code}'
        elif code.startswith(('43', '83', '87')):
            return f'bj.{code}'
        return f'sz.{code}'
    
    @staticmethod
    def baostock_to_code(bs_code):
        """sh.600000 -> 600000"""
        return bs_code.split('.')[-1] if '.' in bs_code else bs_code
    
    def get_trade_dates(self, lookback_days=120):
        """获取最近N个交易日"""
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        
        try:
            with self.session.session() as bs:
                rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
                data = []
                while rs.next():
                    data.append(rs.get_row_data())
                if not data:
                    return []
                df = pd.DataFrame(data, columns=rs.fields)
                df = df[df['is_trading_day'] == '1']
                return df['calendar_date'].tolist()
        except Exception as e:
            logger.warning(f"Baostock获取交易日失败: {e}")
            return []
    
    def get_stock_list(self, date=None):
        """获取股票列表 (带缓存)"""
        if self._stock_list_cache is not None:
            return self._stock_list_cache
        
        try:
            with self.session.session() as bs:
                date = date or datetime.now().strftime('%Y-%m-%d')
                rs = bs.query_all_stock(day=date)
                data = []
                while rs.next():
                    data.append(rs.get_row_data())
                if not data:
                    return pd.DataFrame()
                df = pd.DataFrame(data, columns=rs.fields)
                # 只保留A股
                df = df[df['code'].str.match(r'^(sh|sz)\.\d{6}$')]
                df['raw_code'] = df['code'].apply(self.baostock_to_code)
                self._stock_list_cache = df
                return df
        except Exception as e:
            logger.warning(f"Baostock获取股票列表失败: {e}")
            return pd.DataFrame()
    
    def get_daily_kline(self, code, start_date, end_date, adjustflag='2'):
        """获取日K线 (qfq前复权)"""
        bs_code = self.code_to_baostock(code)
        
        try:
            with self.session.session() as bs:
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,code,open,high,low,close,volume,amount,turn,pctChg,preclose",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag=adjustflag
                )
                data = []
                while rs.next():
                    data.append(rs.get_row_data())
                if not data:
                    return pd.DataFrame()
                df = pd.DataFrame(data, columns=rs.fields)
                # 类型转换
                for col in ['open', 'high', 'low', 'close', 'volume', 'amount', 
                            'turn', 'pctChg', 'preclose']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                df['date'] = pd.to_datetime(df['date'])
                return df
        except Exception as e:
            logger.warning(f"Baostock日K获取失败 {code}: {e}")
            return pd.DataFrame()
    
    def get_5min_kline(self, code, date):
        """获取指定日期的5分钟K线 (用于9:50反包识别)"""
        bs_code = self.code_to_baostock(code)
        date_str = date if '-' in date else f"{date[:4]}-{date[4:6]}-{date[6:]}"
        
        try:
            with self.session.session() as bs:
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,time,code,open,high,low,close,volume,amount",
                    start_date=date_str,
                    end_date=date_str,
                    frequency="5",
                    adjustflag="2"
                )
                data = []
                while rs.next():
                    data.append(rs.get_row_data())
                if not data:
                    return pd.DataFrame()
                df = pd.DataFrame(data, columns=rs.fields)
                for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                # 解析time字段 (格式: 20260430093500000)
                df['datetime'] = pd.to_datetime(df['time'].str[:14], format='%Y%m%d%H%M%S', errors='coerce')
                return df.sort_values('datetime').reset_index(drop=True)
        except Exception as e:
            logger.warning(f"Baostock 5min获取失败 {code}: {e}")
            return pd.DataFrame()


# ============================================================================
# 数据源2: AKShare (打板/龙虎榜独有数据)
# ============================================================================
class AKShareProvider:
    """AKShare数据提供者 - 负责涨停板/龙虎榜/炸板池"""
    
    def __init__(self):
        try:
            import akshare as ak
            self.ak = ak
            logger.info(f"AKShare 版本: {ak.__version__}")
            self.available = True
        except ImportError:
            logger.warning("AKShare未安装")
            self.ak = None
            self.available = False
    
    def get_spot_data(self):
        """A股全市场快照"""
        if not self.available:
            return pd.DataFrame()
        df = safe_call(self.ak.stock_zh_a_spot_em)
        if df is None or df.empty:
            return pd.DataFrame()
        col_map = {
            '代码': 'code', '名称': 'name', '最新价': 'close',
            '涨跌幅': 'pct_chg', '成交量': 'volume', '成交额': 'amount',
            '今开': 'open', '昨收': 'pre_close',
            '总市值': 'total_mv', '流通市值': 'circ_mv',
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        if 'code' in df.columns:
            df = df[~df['code'].str.startswith(('8', '4'))]
            df = df[~df['name'].str.contains('ST|退', na=False)]
        return df.reset_index(drop=True)
    
    def get_zt_pool(self, date):
        """涨停板池"""
        if not self.available:
            return pd.DataFrame()
        return safe_call(self.ak.stock_zt_pool_em, date=date) or pd.DataFrame()
    
    def get_zb_pool(self, date):
        """炸板池"""
        if not self.available:
            return pd.DataFrame()
        return safe_call(self.ak.stock_zt_pool_zbgc_em, date=date) or pd.DataFrame()
    
    def get_lhb_detail(self, date):
        """龙虎榜明细"""
        if not self.available:
            return pd.DataFrame()
        return safe_call(self.ak.stock_lhb_detail_em, 
                         start_date=date, end_date=date) or pd.DataFrame()
    
    def get_5min_kline_fallback(self, code, date):
        """AKShare的5分钟K线 (Baostock失败时备用)"""
        if not self.available:
            return pd.DataFrame()
        df = safe_call(self.ak.stock_zh_a_hist_min_em, 
                      symbol=code, period='5', adjust="qfq")
        if df is None or df.empty:
            return pd.DataFrame()
        col_map = {
            '时间': 'datetime', '开盘': 'open', '收盘': 'close',
            '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
        return df


# ============================================================================
# 统一数据访问层 (混合源调度)
# ============================================================================
class DataHub:
    """统一数据访问层 - 智能路由到最佳数据源"""
    
    def __init__(self):
        self.bao = BaostockProvider() if Config.USE_BAOSTOCK else None
        self.ak = AKShareProvider() if Config.USE_AKSHARE else None
        self._zt_history_cache = {}
    
    def shutdown(self):
        if self.bao:
            self.bao.session.logout()
    
    # ============ 交易日 ============
    def get_trade_date(self, offset=0):
        """获取交易日 (offset=0今日,-1昨日)"""
        # 优先Baostock
        if self.bao:
            dates = self.bao.get_trade_dates(lookback_days=30)
            if dates:
                today = datetime.now().strftime('%Y-%m-%d')
                past = [d for d in dates if d <= today]
                if past:
                    idx = len(past) + offset - 1
                    idx = max(0, min(idx, len(past) - 1))
                    return past[idx].replace('-', '')
        # AKShare兜底
        if self.ak and self.ak.available:
            try:
                cal = safe_call(self.ak.ak.tool_trade_date_hist_sina)
                if cal is not None and not cal.empty:
                    cal['trade_date'] = pd.to_datetime(cal['trade_date'])
                    today = pd.Timestamp.now().normalize()
                    past = cal[cal['trade_date'] <= today].sort_values('trade_date')
                    if not past.empty:
                        idx = len(past) + offset - 1
                        idx = max(0, min(idx, len(past) - 1))
                        return past.iloc[idx]['trade_date'].strftime('%Y%m%d')
            except:
                pass
        return datetime.now().strftime('%Y%m%d')
    
    # ============ 全市场快照 ============
    def get_spot(self):
        """全市场快照 - AKShare优先"""
        if self.ak and self.ak.available:
            df = self.ak.get_spot_data()
            if not df.empty:
                return df
        # Baostock备用 (用最新K线模拟spot)
        logger.info("AKShare快照失败,使用Baostock K线兜底...")
        return self._spot_from_baostock()
    
    def _spot_from_baostock(self):
        """用Baostock K线构造spot快照 (兜底方案)"""
        if not self.bao:
            return pd.DataFrame()
        
        stock_list = self.bao.get_stock_list()
        if stock_list.empty:
            return pd.DataFrame()
        
        # 取最近2日K线计算涨跌幅
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
        
        results = []
        # 注意:全市场5000+股票循环成本高,这里只是兜底,
        # 实际兜底建议先取头部活跃股
        sample_codes = stock_list['raw_code'].head(500).tolist()  # 限制500只
        logger.warning(f"Baostock兜底快照只取前500只股票 (避免接口压力)")
        
        for code in sample_codes:
            try:
                kline = self.bao.get_daily_kline(code, start_date, end_date)
                if len(kline) < 1:
                    continue
                latest = kline.iloc[-1]
                results.append({
                    'code': code,
                    'name': '',
                    'close': latest['close'],
                    'open': latest['open'],
                    'pre_close': latest.get('preclose', latest['close']),
                    'pct_chg': latest['pctChg'],
                    'amount': latest['amount'],
                    'volume': latest['volume'],
                    'turnover': latest.get('turn', 0),
                })
            except:
                continue
        
        return pd.DataFrame(results)
    
    # ============ 涨停板池 ============
    def get_zt_pool(self, date):
        """涨停板池 - AKShare独有"""
        if self.ak and self.ak.available:
            df = self.ak.get_zt_pool(date)
            if not df.empty:
                return df
        # Baostock兜底:用K线推算涨停股
        logger.info(f"AKShare涨停池失败,使用K线推算 {date}")
        return self._zt_pool_from_kline(date)
    
    def _zt_pool_from_kline(self, date):
        """从K线推算涨停板 (兜底,精度较低)"""
        if not self.bao:
            return pd.DataFrame()
        
        date_str = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else date
        stock_list = self.bao.get_stock_list(date_str)
        if stock_list.empty:
            return pd.DataFrame()
        
        zt_stocks = []
        # 性能考虑:这里也只能取部分,完整版需要预先全市场扫描入库
        for _, row in stock_list.head(1000).iterrows():
            code = row['raw_code']
            try:
                kline = self.bao.get_daily_kline(code, date_str, date_str)
                if kline.empty:
                    continue
                k = kline.iloc[0]
                # 涨停判定阈值
                threshold = (Config.ZT_PCT_THRESHOLD_CYB 
                            if code.startswith(('30', '68')) 
                            else Config.ZT_PCT_THRESHOLD)
                if k['pctChg'] >= threshold:
                    zt_stocks.append({
                        '代码': code, '名称': '',
                        '最新价': k['close'], '成交额': k['amount'],
                        '连板数': 1,  # 单日无法判断真实连板,需要历史回看
                        '封板资金': 0,  # K线无法获取
                    })
            except:
                continue
        return pd.DataFrame(zt_stocks)
    
    def get_zb_pool(self, date):
        """炸板池 - AKShare独有 (Baostock无法替代)"""
        if self.ak and self.ak.available:
            return self.ak.get_zb_pool(date)
        return pd.DataFrame()
    
    def get_lhb_detail(self, date):
        """龙虎榜 - AKShare独有"""
        if self.ak and self.ak.available:
            return self.ak.get_lhb_detail(date)
        return pd.DataFrame()
    
    # ============ K线数据 ============
    def get_5min_kline(self, code, date):
        """5分钟K线 - Baostock优先"""
        if Config.PREFER_BAOSTOCK_FOR_KLINE and self.bao:
            df = self.bao.get_5min_kline(code, date)
            if not df.empty:
                return df
        if self.ak and self.ak.available:
            return self.ak.get_5min_kline_fallback(code, date)
        return pd.DataFrame()
    
    def get_daily_kline(self, code, days=30):
        """日K线 - Baostock优先"""
        end = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=days*2)).strftime('%Y-%m-%d')
        
        if self.bao:
            df = self.bao.get_daily_kline(code, start, end)
            if not df.empty:
                # 字段适配为统一格式
                df = df.rename(columns={'pctChg': 'pct_chg', 'turn': 'turnover'})
                return df.tail(days).reset_index(drop=True)
        
        if self.ak and self.ak.available:
            try:
                df = safe_call(
                    self.ak.ak.stock_zh_a_hist,
                    symbol=code, period="daily",
                    start_date=start.replace('-',''), 
                    end_date=end.replace('-',''),
                    adjust="qfq"
                )
                if df is not None and not df.empty:
                    col_map = {'日期': 'date', '开盘': 'open', '收盘': 'close',
                               '最高': 'high', '最低': 'low', '成交量': 'volume',
                               '成交额': 'amount', '涨跌幅': 'pct_chg', '换手率': 'turnover'}
                    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
                    df['date'] = pd.to_datetime(df['date'])
                    return df.tail(days).reset_index(drop=True)
            except:
                pass
        return pd.DataFrame()


# ============================================================================
# Layer 1: 市场强势期判断
# ============================================================================
class MarketRegime:
    def __init__(self, hub: DataHub):
        self.hub = hub
    
    def judge(self, date):
        zt_yesterday = self.hub.get_zt_pool(date)
        spot = self.hub.get_spot()
        
        if spot.empty or 'amount' not in spot.columns:
            return 'WEAK', 0.0, {'error': 'no_spot_data'}
        
        spot_valid = spot[(spot['amount'] > 1e8) & (spot['pct_chg'].abs() < 25)].copy()
        spot_valid = spot_valid.nlargest(Config.TOP_N_BY_AMOUNT, 'amount')
        
        if len(spot_valid) < Config.TOP_N_BY_AMOUNT:
            return 'WEAK', 0.0, {'error': 'insufficient_data'}
        
        up_count = (spot_valid['pct_chg'] > 0).sum()
        avg_pct = spot_valid['pct_chg'].mean()
        
        detail = {
            'top5_codes': spot_valid['code'].tolist(),
            'top5_names': spot_valid['name'].tolist() if 'name' in spot_valid.columns else [],
            'top5_pct_chg': spot_valid['pct_chg'].round(2).tolist(),
            'up_count': int(up_count),
            'avg_pct_chg': round(float(avg_pct), 2),
            'zt_count_yesterday': len(zt_yesterday),
        }
        
        if up_count >= Config.HEAVY_THRESHOLD:
            return 'STRONG', 1.0, detail
        elif up_count >= Config.STRONG_THRESHOLD:
            return 'NORMAL', 0.6, detail
        else:
            return 'WEAK', 0.0, detail


# ============================================================================
# Layer 2: 选股方向
# ============================================================================
class StockSelector:
    def __init__(self, hub: DataHub):
        self.hub = hub
        self._zt_history_cache = {}
    
    def get_zt_history(self, end_date, lookback_days=90):
        if end_date in self._zt_history_cache:
            return self._zt_history_cache[end_date]
        
        all_zt = []
        end_dt = datetime.strptime(end_date, '%Y%m%d')
        for i in range(lookback_days):
            date_i = (end_dt - timedelta(days=i)).strftime('%Y%m%d')
            df = self.hub.get_zt_pool(date_i)
            if not df.empty:
                df = df.copy()
                df['trade_date'] = date_i
                all_zt.append(df)
        
        result = pd.concat(all_zt, ignore_index=True) if all_zt else pd.DataFrame()
        self._zt_history_cache[end_date] = result
        return result
    
    @staticmethod
    def _get_col(df, candidates):
        """从候选名中找出第一个存在的列名"""
        for c in candidates:
            if c in df.columns:
                return c
        return None
    
    def select_hot_stocks(self, date):
        zt = self.hub.get_zt_pool(date)
        if zt.empty:
            return pd.DataFrame()
        
        amount_col = self._get_col(zt, ['成交额', 'amount'])
        lianban_col = self._get_col(zt, ['连板数', 'lianban'])
        code_col = self._get_col(zt, ['代码', 'code'])
        name_col = self._get_col(zt, ['名称', 'name'])
        
        if not all([amount_col, lianban_col, code_col]):
            logger.warning(f"涨停板数据字段不全: {zt.columns.tolist()}")
            return pd.DataFrame()
        
        hot = zt[
            (zt[amount_col] > Config.HOT_AMOUNT_THRESHOLD) & 
            (zt[lianban_col] >= 2)
        ].copy()
        
        if hot.empty:
            return pd.DataFrame()
        
        return pd.DataFrame({
            'code': hot[code_col],
            'name': hot[name_col] if name_col else '',
            'amount': hot[amount_col],
            'lianban': hot[lianban_col],
            'category': 'HOT',
        }).reset_index(drop=True)
    
    def select_popular_stocks(self, date):
        zt_history = self.get_zt_history(date, Config.RECENT_4BAN_DAYS)
        if zt_history.empty:
            return pd.DataFrame()
        
        code_col = self._get_col(zt_history, ['代码', 'code'])
        name_col = self._get_col(zt_history, ['名称', 'name'])
        lianban_col = self._get_col(zt_history, ['连板数', 'lianban'])
        amount_col = self._get_col(zt_history, ['成交额', 'amount'])
        
        if not all([code_col, lianban_col]):
            return pd.DataFrame()
        
        end_dt = datetime.strptime(date, '%Y%m%d')
        recent_10 = zt_history[
            pd.to_datetime(zt_history['trade_date']) >= (end_dt - timedelta(days=Config.RECENT_2BAN_DAYS))
        ]
        max_lianban_10 = recent_10.groupby(code_col)[lianban_col].max()
        candidates_2ban = set(max_lianban_10[max_lianban_10 >= 2].index.tolist())
        
        max_lianban_90 = zt_history.groupby(code_col)[lianban_col].max()
        candidates_4ban = set(max_lianban_90[max_lianban_90 >= 4].index.tolist())
        
        today_zt = self.hub.get_zt_pool(date)
        if today_zt.empty:
            return pd.DataFrame()
        today_codes = set(today_zt[code_col].tolist())
        
        popular_codes = today_codes & (candidates_2ban | candidates_4ban)
        if not popular_codes:
            return pd.DataFrame()
        
        result = today_zt[today_zt[code_col].isin(popular_codes)].copy()
        return pd.DataFrame({
            'code': result[code_col],
            'name': result[name_col] if name_col else '',
            'amount': result[amount_col] if amount_col else 0,
            'lianban': result[lianban_col],
            'category': 'POPULAR',
        }).reset_index(drop=True)
    
    def select_near_zt_stocks(self, date):
        """
        准涨停池: 涨幅7%-9.5% + 量比>2 + 成交额>5亿
        次日溢价统计支持，但权重打8折
        """
        spot = self.hub.get_spot()
        if spot.empty:
            return pd.DataFrame()
        
        pct_col = self._get_col(spot, ['涨跌幅', 'pct_chg'])
        amount_col = self._get_col(spot, ['成交额', 'amount'])
        code_col = self._get_col(spot, ['代码', 'code'])
        name_col = self._get_col(spot, ['名称', 'name'])
        vol_col = self._get_col(spot, ['量比', 'vol_ratio'])
        
        if not all([pct_col, amount_col, code_col]):
            logger.warning(f"快照数据字段不全: {spot.columns.tolist()}")
            return pd.DataFrame()
        
        # 涨幅7%-9.5%
        near_zt = spot[
            (spot[pct_col] >= Config.NEAR_ZT_PCT_LO) &
            (spot[pct_col] <= Config.NEAR_ZT_PCT_HI)
        ].copy()
        
        if near_zt.empty:
            return pd.DataFrame()
        
        # 成交额>5亿
        if amount_col and amount_col in near_zt.columns:
            near_zt = near_zt[near_zt[amount_col] > 5e8]
        
        # 量比>2 (如果有量比数据)
        if vol_col and vol_col in near_zt.columns:
            near_zt = near_zt[near_zt[vol_col] >= Config.NEAR_ZT_VOL_RATIO_MIN]
        
        if near_zt.empty:
            return pd.DataFrame()
        
        return pd.DataFrame({
            'code': near_zt[code_col],
            'name': near_zt[name_col] if name_col else '',
            'amount': near_zt[amount_col] if amount_col else 0,
            'lianban': 0,
            'category': 'NEAR_ZT',
            'pct': near_zt[pct_col],
        }).reset_index(drop=True)


# ============================================================================
# Layer 3: 9:50反包形态识别 (优先用Baostock)
# ============================================================================
class PatternFilter:
    def __init__(self, hub: DataHub):
        self.hub = hub
    
    def detect_950_reversal(self, code, date) -> Tuple[bool, float]:
        """
        9:50反包形态:
            - 9:30-9:50之间出现下杀(>1%)
            - 9:50后被资金合力拉起(反弹>2%)
            - 收盘高于开盘
        """
        try:
            df = self.hub.get_5min_kline(code, date)
            if df.empty or len(df) < 5:
                return False, 0.0
            
            # 取交易日内的数据
            if 'datetime' in df.columns:
                df = df.sort_values('datetime').reset_index(drop=True)
            
            early = df.head(4)  # 9:30-9:50四根5分钟K
            after_950 = df.iloc[4:8] if len(df) >= 8 else df.iloc[4:]
            
            if early.empty or after_950.empty:
                return False, 0.0
            
            open_price = float(early.iloc[0]['open'])
            early_low = float(early['low'].min())
            after_high = float(after_950['high'].max())
            after_close = float(after_950.iloc[-1]['close'])
            
            if open_price <= 0 or early_low <= 0:
                return False, 0.0
            
            dip_pct = (early_low - open_price) / open_price
            recovery_pct = (after_high - early_low) / early_low
            
            is_reversal = (dip_pct < -0.01) and (recovery_pct > 0.02) and (after_close > open_price)
            strength = min(recovery_pct / 0.05, 1.0) if is_reversal else 0.0
            
            return is_reversal, strength
        except Exception as e:
            logger.debug(f"9:50反包识别失败 {code}: {e}")
            return False, 0.0


# ============================================================================
# Layer 4: 资金面因子
# ============================================================================
class CapitalFactors:
    def __init__(self, hub: DataHub):
        self.hub = hub
        self._lhb_cache = {}
    
    def get_lhb_data(self, end_date, lookback_days=5):
        cache_key = f"{end_date}_{lookback_days}"
        if cache_key in self._lhb_cache:
            return self._lhb_cache[cache_key]
        
        all_lhb = []
        end_dt = datetime.strptime(end_date, '%Y%m%d')
        for i in range(lookback_days):
            d = (end_dt - timedelta(days=i)).strftime('%Y%m%d')
            df = self.hub.get_lhb_detail(d)
            if not df.empty:
                df = df.copy()
                df['trade_date'] = d
                all_lhb.append(df)
        
        result = pd.concat(all_lhb, ignore_index=True) if all_lhb else pd.DataFrame()
        self._lhb_cache[cache_key] = result
        return result
    
    def institutional_net_buy(self, code, lhb_data) -> float:
        if lhb_data.empty:
            return 0.0
        code_col = '代码' if '代码' in lhb_data.columns else 'code'
        net_col = next((c for c in ['机构买入净额', '净买额', '净额'] 
                        if c in lhb_data.columns), None)
        if not net_col:
            return 0.0
        sub = lhb_data[lhb_data[code_col] == code]
        if sub.empty:
            return 0.0
        return float(sub[net_col].sum() / 10000)
    
    def youzi_seat_activity(self, code, lhb_data) -> int:
        if lhb_data.empty:
            return 0
        code_col = '代码' if '代码' in lhb_data.columns else 'code'
        return int((lhb_data[code_col] == code).sum())
    
    def seal_amount_ratio(self, code, zt_pool, spot_data) -> float:
        if zt_pool.empty:
            return 0.0
        code_col = '代码' if '代码' in zt_pool.columns else 'code'
        seal_col = next((c for c in ['封板资金', '封单金额', '封板金额'] 
                         if c in zt_pool.columns), None)
        if not seal_col:
            return 0.0
        zt_row = zt_pool[zt_pool[code_col] == code]
        if zt_row.empty:
            return 0.0
        seal_amount = float(zt_row[seal_col].iloc[0])
        
        circ_mv = 0
        if not spot_data.empty:
            spot_row = spot_data[spot_data['code'] == code]
            if not spot_row.empty and 'circ_mv' in spot_row.columns:
                circ_mv = float(spot_row['circ_mv'].iloc[0])
        
        return seal_amount / circ_mv if circ_mv > 0 else 0.0


# ============================================================================
# Layer 5: 板块情绪
# ============================================================================
class SectorSentiment:
    def __init__(self, hub: DataHub):
        self.hub = hub
    
    def calc_market_sentiment(self, date) -> Dict:
        zt = self.hub.get_zt_pool(date)
        zb = self.hub.get_zb_pool(date)
        
        zt_count = len(zt) if not zt.empty else 0
        zb_count = len(zb) if not zb.empty else 0
        
        total_attempts = zt_count + zb_count
        blockup_rate = zb_count / total_attempts if total_attempts > 0 else 0.0
        
        # 新增: 跌停家数 (恐慌指标)
        dt_count = self._get_dt_count(date)
        
        # 新增: 昨日涨停今日溢价率
        zt_premium = self._calc_zt_premium(date)
        
        return {
            'zt_count': zt_count,
            'zb_count': zb_count,
            'blockup_rate': round(blockup_rate, 4),
            'dt_count': dt_count,
            'zt_premium': round(zt_premium, 4),
            'sentiment_score': self._score_sentiment(zt_count, blockup_rate, dt_count, zt_premium),
        }
    
    def _get_dt_count(self, date) -> int:
        """获取跌停家数"""
        if not self.hub.ak or not self.hub.ak.available:
            return 0
        try:
            dt_pool = safe_call(self.hub.ak.ak.stock_zt_pool_dtgc_em, date=date)
            if dt_pool is not None and not dt_pool.empty:
                return len(dt_pool)
        except Exception as e:
            logger.warning(f"获取跌停池失败: {e}")
        return 0
    
    def _calc_zt_premium(self, date) -> float:
        """
        计算昨日涨停今日溢价率
        溢价率 = (今日收盘价 - 昨日涨停价) / 昨日涨停价
        """
        try:
            # 获取昨日涨停池
            prev_date = self.hub.get_trade_date(offset=-1)
            if prev_date == date:
                return 0.0
            
            zt_yesterday = self.hub.get_zt_pool(prev_date)
            if zt_yesterday.empty:
                return 0.0
            
            code_col = self._get_col(zt_yesterday, ['代码', 'code'])
            if not code_col:
                return 0.0
            
            # 获取今日行情
            spot = self.hub.get_spot()
            if spot.empty:
                return 0.0
            
            spot_code_col = self._get_col(spot, ['代码', 'code'])
            spot_close_col = self._get_col(spot, ['涨跌幅', 'pct_chg'])
            
            if not all([spot_code_col, spot_close_col]):
                return 0.0
            
            # 计算平均溢价 (用今日涨跌幅近似)
            yesterday_codes = set(zt_yesterday[code_col].tolist())
            today_data = spot[spot[spot_code_col].isin(yesterday_codes)]
            
            if today_data.empty:
                return 0.0
            
            avg_premium = today_data[spot_close_col].mean()
            return avg_premium
        except Exception as e:
            logger.warning(f"计算涨停溢价率失败: {e}")
            return 0.0
    
    def _score_sentiment(self, zt_count, blockup_rate, dt_count, zt_premium):
        # 涨停家数得分
        if zt_count >= 80: zt_score = 30
        elif zt_count >= 50: zt_score = 25
        elif zt_count >= 30: zt_score = 15
        elif zt_count >= 15: zt_score = 8
        else: zt_score = 0
        
        # 炸板率得分
        if blockup_rate < 0.10: zb_score = 25
        elif blockup_rate < 0.20: zb_score = 20
        elif blockup_rate < 0.30: zb_score = 12
        elif blockup_rate < 0.40: zb_score = 5
        else: zb_score = 0
        
        # 跌停家数得分 (恐慌指标)
        if dt_count == 0: dt_score = 20
        elif dt_count <= 3: dt_score = 15
        elif dt_count <= 5: dt_score = 8
        else: dt_score = 0  # 跌停>5说明恐慌
        
        # 涨停溢价率得分
        if zt_premium > 3: premium_score = 25
        elif zt_premium > 1: premium_score = 18
        elif zt_premium > 0: premium_score = 10
        else: premium_score = 0  # 负溢价说明亏钱效应
        
        return zt_score + zb_score + dt_score + premium_score


# ============================================================================
# 综合打分
# ============================================================================
class ScoreEngine:
    WEIGHTS = {
        'lianban_score': 15,
        'amount_score': 10,
        'lhb_inst_score': 20,
        'youzi_score': 15,
        'seal_ratio_score': 15,
        'pattern_score': 10,
        'sentiment_score': 15,
    }
    
    def score_stock(self, stock: Dict, sentiment: Dict) -> Dict:
        scores = {}
        
        lb = stock.get('lianban', 1)
        if lb >= 4: scores['lianban_score'] = 100
        elif lb == 3: scores['lianban_score'] = 80
        elif lb == 2: scores['lianban_score'] = 60
        else: scores['lianban_score'] = 30
        
        amt = stock.get('amount', 0) / 1e8
        if amt >= 30: scores['amount_score'] = 100
        elif amt >= 20: scores['amount_score'] = 80
        elif amt >= 10: scores['amount_score'] = 60
        elif amt >= 5: scores['amount_score'] = 40
        else: scores['amount_score'] = 20
        
        lhb_net = stock.get('lhb_net', 0)
        if lhb_net >= 5000: scores['lhb_inst_score'] = 100
        elif lhb_net >= 2000: scores['lhb_inst_score'] = 80
        elif lhb_net >= 500: scores['lhb_inst_score'] = 60
        elif lhb_net > 0: scores['lhb_inst_score'] = 40
        elif lhb_net == 0: scores['lhb_inst_score'] = 30
        else: scores['lhb_inst_score'] = 10
        
        scores['youzi_score'] = min(stock.get('youzi_count', 0) * 30, 100)
        
        seal_ratio = stock.get('seal_ratio', 0)
        if seal_ratio >= 0.05: scores['seal_ratio_score'] = 100
        elif seal_ratio >= 0.02: scores['seal_ratio_score'] = 80
        elif seal_ratio >= 0.01: scores['seal_ratio_score'] = 60
        elif seal_ratio >= 0.005: scores['seal_ratio_score'] = 40
        else: scores['seal_ratio_score'] = 20
        
        if stock.get('pattern_reversal', False):
            scores['pattern_score'] = int(60 + stock.get('pattern_strength', 0) * 40)
        else:
            scores['pattern_score'] = 30
        
        scores['sentiment_score'] = sentiment.get('sentiment_score', 50)
        
        total = sum(scores[k] * self.WEIGHTS[k] / 100 for k in scores)
        return {'total_score': round(total, 2), 'sub_scores': scores}


# ============================================================================
# 仓位管理
# ============================================================================
class PositionManager:
    @staticmethod
    def calc_position(stock_score, regime_multiplier, num_candidates, 
                      amount=0, lianban=0, category='HOT') -> float:
        """
        动态仓位计算
        
        Args:
            stock_score: 股票得分
            regime_multiplier: 市场状态乘数
            num_candidates: 候选股数量
            amount: 成交额 (用于流动性折扣)
            lianban: 连板数 (5板以上减半)
            category: 类别 (NEAR_ZT 权重打8折)
        """
        if num_candidates <= 0:
            return 0.0
        
        equal_weight = Config.MAX_TOTAL_POSITION / num_candidates
        base = min(equal_weight, Config.MAX_POSITION_PER_STOCK)
        
        if stock_score < Config.MIN_TOTAL_SCORE:
            return 0.0
        
        score_factor = (stock_score - 60) / 40
        score_factor = max(0, min(score_factor, 1.0))
        
        position = base * regime_multiplier * (0.5 + 0.5 * score_factor)
        
        # 流动性折扣: 成交额<5亿仓位减半
        if amount > 0 and amount < Config.MIN_AMOUNT_LIQUIDITY:
            position *= Config.LIQUIDITY_DISCOUNT
        
        # 5板以上仓位减半 (不是3板! 3-4板是龙头确认期)
        if lianban >= Config.HIGH_BOARD_THRESHOLD:
            position *= Config.HIGH_BOARD_DISCOUNT
        
        # 准涨停权重打8折
        if category == 'NEAR_ZT':
            position *= Config.NEAR_ZT_WEIGHT
        
        position = min(position, Config.MAX_POSITION_PER_STOCK)
        return round(position, 4)


# ============================================================================
# 主策略
# ============================================================================
class DabangStrategy:
    def __init__(self):
        self.hub = DataHub()
        self.regime = MarketRegime(self.hub)
        self.selector = StockSelector(self.hub)
        self.pattern = PatternFilter(self.hub)
        self.capital = CapitalFactors(self.hub)
        self.sentiment = SectorSentiment(self.hub)
        self.scorer = ScoreEngine()
        self.pm = PositionManager()
    
    def run(self, target_date=None):
        try:
            if target_date is None:
                target_date = self.hub.get_trade_date(offset=0)
            
            logger.info("=" * 80)
            logger.info(f"短线打板策略 V2.0 (混合数据源) - 数据日期: {target_date}")
            logger.info(f"AKShare: {'启用' if self.hub.ak and self.hub.ak.available else '禁用'}")
            logger.info(f"Baostock: {'启用' if self.hub.bao else '禁用'}")
            logger.info("=" * 80)
            
            # Layer 1
            logger.info("\n[Layer 1] 市场强势期判断...")
            regime, regime_mult, regime_detail = self.regime.judge(target_date)
            logger.info(f"  → {regime} (仓位乘数 {regime_mult})")
            logger.info(f"  → 昨日Top5上涨数: {regime_detail.get('up_count', 0)}/5")
            logger.info(f"  → 昨日Top5平均涨幅: {regime_detail.get('avg_pct_chg', 0)}%")
            
            if regime == 'WEAK':
                logger.warning("⚠️  市场不在强势期,空仓观望")
                self._save_empty_report(target_date, regime, regime_detail)
                return None
            
            # Layer 5
            logger.info("\n[Layer 5] 板块情绪计算...")
            market_sentiment = self.sentiment.calc_market_sentiment(target_date)
            logger.info(f"  → 涨停{market_sentiment['zt_count']}, 炸板{market_sentiment['zb_count']}, "
                       f"炸板率{market_sentiment['blockup_rate']:.2%}")
            
            if market_sentiment['blockup_rate'] > Config.BLOCKUP_RATE_MAX:
                logger.warning(f"⚠️  炸板率过高,仓位再砍半")
                regime_mult *= 0.5
            
            # Layer 2
            logger.info("\n[Layer 2] 选股方向筛选...")
            hot = self.selector.select_hot_stocks(target_date)
            popular = self.selector.select_popular_stocks(target_date)
            near_zt = self.selector.select_near_zt_stocks(target_date)
            logger.info(f"  → 热点票: {len(hot)}, 人气股: {len(popular)}, 准涨停: {len(near_zt)}")
            
            all_candidates = pd.concat([hot, popular, near_zt], ignore_index=True)
            if all_candidates.empty:
                logger.warning("⚠️  无符合方向股票")
                self._save_empty_report(target_date, regime, regime_detail)
                return None
            
            all_candidates = all_candidates.drop_duplicates(subset='code', keep='first').reset_index(drop=True)
            logger.info(f"  → 去重候选: {len(all_candidates)}")
            
            # Layer 4 数据预取
            logger.info("\n[Layer 4] 资金面预取...")
            lhb_data = self.capital.get_lhb_data(target_date, Config.LHB_LOOKBACK_DAYS)
            zt_pool_today = self.hub.get_zt_pool(target_date)
            spot_data = self.hub.get_spot()
            
            # 综合打分
            logger.info("\n[综合打分]")
            scored_stocks = []
            for idx, row in all_candidates.iterrows():
                code = row['code']
                name = row.get('name', '')
                try:
                    is_reversal, strength = self.pattern.detect_950_reversal(code, target_date)
                    lhb_net = self.capital.institutional_net_buy(code, lhb_data)
                    youzi_count = self.capital.youzi_seat_activity(code, lhb_data)
                    seal_ratio = self.capital.seal_amount_ratio(code, zt_pool_today, spot_data)
                    
                    stock_data = {
                        'code': code, 'name': name,
                        'category': row.get('category', ''),
                        'lianban': row.get('lianban', 1),
                        'amount': row.get('amount', 0),
                        'lhb_net': lhb_net, 'youzi_count': youzi_count,
                        'seal_ratio': seal_ratio,
                        'pattern_reversal': is_reversal,
                        'pattern_strength': strength,
                    }
                    score_result = self.scorer.score_stock(stock_data, market_sentiment)
                    stock_data.update(score_result)
                    scored_stocks.append(stock_data)
                    
                    logger.info(f"  [{idx+1}/{len(all_candidates)}] {code} {name} "
                               f"分={score_result['total_score']} 连板={row.get('lianban', 1)} "
                               f"机构净买={lhb_net:.0f}万 封单比={seal_ratio:.2%}")
                except Exception as e:
                    logger.error(f"  ✗ {code} 失败: {e}")
            
            if not scored_stocks:
                logger.warning("⚠️  无成功打分股票")
                self._save_empty_report(target_date, regime, regime_detail)
                return None
            
            scored_df = pd.DataFrame(scored_stocks).sort_values('total_score', ascending=False).reset_index(drop=True)
            qualified = scored_df[scored_df['total_score'] >= Config.MIN_TOTAL_SCORE].head(Config.MAX_CANDIDATES).copy()
            
            if qualified.empty:
                logger.warning(f"⚠️  无股票得分>={Config.MIN_TOTAL_SCORE}")
                self._save_empty_report(target_date, regime, regime_detail)
                return None
            
            n = len(qualified)
            qualified['position'] = qualified.apply(
                lambda row: self.pm.calc_position(
                    row['total_score'], regime_mult, n,
                    amount=row.get('amount', 0),
                    lianban=row.get('lianban', 0),
                    category=row.get('category', 'HOT')
                ), axis=1)
            qualified['stop_loss_price_ratio'] = 1 - Config.MAX_LOSS_PER_TRADE
            
            self._save_report(qualified, target_date, regime, regime_detail, market_sentiment)
            return qualified
        
        finally:
            self.hub.shutdown()
    
    def _save_empty_report(self, date, regime, regime_detail):
        report_path = Config.OUTPUT_DIR / f"dabang_候选池_{date}.md"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"# 短线打板候选池 - {date}\n\n")
            f.write(f"## 信号: 空仓\n\n")
            f.write(f"- 市场状态: **{regime}**\n")
            f.write(f"- 昨日Top5上涨数: {regime_detail.get('up_count', 0)}/5\n")
            f.write(f"- 昨日Top5平均涨幅: {regime_detail.get('avg_pct_chg', 0)}%\n\n")
            f.write("**操作建议**: 不开新仓,等待强势期重启\n")
        logger.info(f"\n空仓报告: {report_path}")
    
    def _save_report(self, df, date, regime, regime_detail, sentiment):
        csv_path = Config.OUTPUT_DIR / f"dabang_候选池_{date}.csv"
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        
        md_path = Config.OUTPUT_DIR / f"dabang_候选池_{date}.md"
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(f"# 短线打板候选池 - {date}\n\n")
            f.write(f"## 一、市场状态\n\n")
            f.write(f"- **强势期**: {regime}\n")
            f.write(f"- **昨日Top5上涨**: {regime_detail.get('up_count', 0)}/5\n")
            f.write(f"- **涨停家数**: {sentiment['zt_count']} | **炸板率**: {sentiment['blockup_rate']:.2%}\n")
            f.write(f"- **跌停家数**: {sentiment.get('dt_count', 0)} (恐慌指标)\n")
            f.write(f"- **昨日涨停今日溢价**: {sentiment.get('zt_premium', 0):.2f}%\n")
            f.write(f"- **情绪得分**: {sentiment['sentiment_score']}/100\n\n")
            
            f.write(f"## 二、候选池 (共{len(df)}只)\n\n")
            f.write("| # | 代码 | 名称 | 类别 | 连板 | 得分 | 机构净买(万) | 游资席位 | 封单比 | 9:50反包 | 仓位 |\n")
            f.write("|---|------|------|------|------|------|--------------|----------|--------|----------|------|\n")
            for i, row in df.iterrows():
                f.write(f"| {i+1} | {row['code']} | {row['name']} | {row['category']} | "
                       f"{row['lianban']} | {row['total_score']} | "
                       f"{row['lhb_net']:.0f} | {row['youzi_count']} | "
                       f"{row['seal_ratio']:.2%} | "
                       f"{'✓' if row['pattern_reversal'] else '✗'} | "
                       f"{row['position']:.2%} |\n")
            
            f.write(f"\n## 三、风控参数\n\n")
            f.write(f"- 单票止损: **{Config.MAX_LOSS_PER_TRADE:.0%}**\n")
            f.write(f"- 单票最大仓位: **{Config.MAX_POSITION_PER_STOCK:.0%}**\n")
            f.write(f"- 总仓位上限: **{Config.MAX_TOTAL_POSITION:.0%}** (实际占用: {df['position'].sum():.2%})\n\n")
            
            f.write(f"## 四、操作纪律\n\n")
            f.write("1. 次日开盘前在Futu设置候选股价格预警\n")
            f.write("2. 9:50关键时点优先做反包票\n")
            f.write("3. 不追开盘直拉票\n")
            f.write("4. 打板次日拉高即出,不奢求连板\n")
            f.write("5. 跌破成本价3%立即止损\n")
            f.write("6. 次日Top5上涨<3支,全部清仓\n")
        
        logger.info(f"\n✅ CSV: {csv_path}")
        logger.info(f"✅ MD:  {md_path}")
        logger.info(f"\n候选池Top5:\n{df.head().to_string()}")


# ============================================================================
# 入口
# ============================================================================
def main():
    try:
        strategy = DabangStrategy()
        result = strategy.run()
        if result is not None:
            print(f"\n{'='*80}")
            print(f"✅ 完成,候选股 {len(result)} 只")
            print(f"📂 报告目录: {Config.OUTPUT_DIR}")
            print(f"{'='*80}")
    except Exception as e:
        logger.error(f"策略异常: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    main()