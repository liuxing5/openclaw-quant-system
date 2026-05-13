"""
数据访问层 — baostock 版本
================================================
- 唯一数据源: baostock (Tushare/akshare 都已替换)
- 文件级缓存,降低 baostock 压力
- 自动重试 + 指数退避
- 全局登录管理(进程级单例)

baostock 关键差异:
  - 必须 bs.login() 才能查询
  - 股票代码格式: "sh.600000" / "sz.000001"
  - 复权参数 adjustflag: "1"=后复权 "2"=前复权 "3"=不复权
  - 返回 ResultData 对象,需循环 get_row_data() 转 DataFrame

L8 (主力资金流) 和 L8.5 (龙虎榜) baostock 无对应接口,
对应方法返回 None,筛选器会按 allow_*_missing=True 默认跳过。
"""
from __future__ import annotations

import atexit
import hashlib
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import baostock as bs
import pandas as pd

from config import DataConfig, TZ

logger = logging.getLogger(__name__)


# ============================================================
# baostock 全局会话管理(进程级单例,线程安全)
# ============================================================
_LOGIN_LOCK = threading.Lock()
_LOGGED_IN = False


def ensure_logged_in() -> None:
    """惰性登录 baostock,失败抛异常"""
    global _LOGGED_IN
    if _LOGGED_IN:
        return
    with _LOGIN_LOCK:
        if _LOGGED_IN:
            return
        result = bs.login()
        if result.error_code != "0":
            raise RuntimeError(
                f"baostock 登录失败: {result.error_code} {result.error_msg}"
            )
        _LOGGED_IN = True
        logger.info(f"baostock 已登录: {result.error_msg}")


def _logout_at_exit():
    global _LOGGED_IN
    if _LOGGED_IN:
        try:
            bs.logout()
            _LOGGED_IN = False
        except Exception:
            pass


atexit.register(_logout_at_exit)


# ============================================================
# 工具
# ============================================================
def to_bs_code(symbol: str) -> str:
    """6 位代码 -> baostock 格式"""
    if "." in symbol:
        return symbol
    s = symbol.strip()
    if s.startswith(("6", "9")):
        return f"sh.{s}"
    if s.startswith(("0", "2", "3")):
        return f"sz.{s}"
    if s.startswith(("4", "8")):
        return f"bj.{s}"
    return f"sh.{s}"


def from_bs_code(bs_code: str) -> str:
    """sh.600000 -> 600000"""
    if "." in bs_code:
        return bs_code.split(".", 1)[1]
    return bs_code


def to_bs_index(symbol: str) -> str:
    """sh000300 -> sh.000300"""
    s = symbol.strip()
    if "." in s:
        return s
    if s.startswith("sh"):
        return f"sh.{s[2:]}"
    if s.startswith("sz"):
        return f"sz.{s[2:]}"
    return s


# ============================================================
class DataLoader:
    def __init__(self, cfg: Optional[DataConfig] = None):
        self.cfg = cfg or DataConfig()
        self.cache_dir = Path(self.cfg.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -------------------- 缓存 --------------------
    def _cache_path(self, key: str) -> Path:
        h = hashlib.md5(key.encode('utf-8')).hexdigest()[:16]
        return self.cache_dir / f"{h}.parquet"

    def _get_cache_ttl_hours(self) -> int:
        """
        智能缓存 TTL:
        - 盘中时段 (9:30-15:30): 2 小时 (快速刷新)
        - 盘后时段 (15:30-次日 9:00): 24 小时 (隔夜有效)
        """
        now = datetime.now(TZ)
        hour = now.hour
        minute = now.minute
        
        # 盘中时段：9:30-15:30
        if (hour == 9 and minute >= 30) or (10 <= hour < 15) or (hour == 15 and minute <= 30):
            return 2  # 盘中 2 小时刷新
        
        # 盘后时段：15:30-次日 9:00
        return 24  # 盘后 24 小时刷新
    
    def _read_cache(self, key: str) -> Optional[pd.DataFrame]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        # 使用智能 TTL
        ttl_hours = self._get_cache_ttl_hours()
        if age > ttl_hours * 3600:
            return None
        try:
            return pd.read_parquet(path)
        except Exception:
            return None

    def _write_cache(self, key: str, df: pd.DataFrame) -> None:
        try:
            df.to_parquet(self._cache_path(key))
        except Exception as e:
            logger.debug(f"缓存写入失败 {key}: {e}")

    def _retry(self, fn, *args, **kwargs):
        last_err = None
        for i in range(self.cfg.max_retries):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last_err = e
                wait = self.cfg.retry_backoff ** i
                logger.debug(f"重试 {i+1}/{self.cfg.max_retries}, 等待 {wait:.1f}s: {e}")
                time.sleep(wait)
        raise last_err

    # -------------------- baostock 查询封装 --------------------
    @staticmethod
    def _rs_to_df(rs) -> pd.DataFrame:
        if rs.error_code != "0":
            raise RuntimeError(f"baostock 查询失败: {rs.error_code} {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return pd.DataFrame(columns=rs.fields)
        return pd.DataFrame(rows, columns=rs.fields)

    # -------------------- 个股 K 线 --------------------
    def get_kline(self, symbol: str, days: int = 300,
                  end_date: Optional[str] = None,
                  adjust: str = "qfq") -> Optional[pd.DataFrame]:
        """
        默认前复权(qfq):
          - 真实价格,可以直接用于止损价、仓位计算、撮合
          - 历史价格不会被异常抬升(后复权茅台 10000+ 元的笑话)
          - 趋势/百分比指标(MACD/均线/回撤百分比)结果与后复权等价
        adjust 选项: "qfq"=前复权(默认), "hfq"=后复权, "none"=不复权
        baostock adjustflag: "1"=后复权 "2"=前复权 "3"=不复权
        """
        adjust_map = {"hfq": "1", "qfq": "2", "none": "3"}
        adjustflag = adjust_map.get(adjust, "2")

        end = end_date or datetime.now(TZ).strftime("%Y%m%d")
        end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
        start_fmt = (datetime.strptime(end, "%Y%m%d") - timedelta(days=days * 2)).strftime("%Y-%m-%d")

        bs_code = to_bs_code(symbol)
        key = f"kline_bs_{bs_code}_{start_fmt}_{end_fmt}_{adjustflag}"

        cached = self._read_cache(key)
        if cached is not None:
            return cached.tail(days).reset_index(drop=True)

        try:
            ensure_logged_in()
            rs = self._retry(
                bs.query_history_k_data_plus,
                bs_code,
                "date,open,high,low,close,volume,amount,turn,pctChg",
                start_date=start_fmt,
                end_date=end_fmt,
                frequency="d",
                adjustflag=adjustflag,
            )
            df = self._rs_to_df(rs)
            if df.empty:
                return None

            df = df.rename(columns={"turn": "turnover", "pctChg": "pct_change"})
            df["date"] = pd.to_datetime(df["date"])
            for col in ["open", "high", "low", "close", "amount", "turnover", "pct_change"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")

            # baostock 的 pct_change 单位是百分比(2.5 表示 2.5%),转换为 0.025
            if "pct_change" in df.columns:
                df["pct_change"] = df["pct_change"] / 100.0

            df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
            self._write_cache(key, df)
            return df.tail(days).reset_index(drop=True)
        except Exception as e:
            logger.warning(f"{symbol} K 线获取失败: {e}")
            return None

    # -------------------- L8: baostock 不支持 --------------------
    def get_money_flow(self, symbol: str, days: int = 10) -> Optional[pd.DataFrame]:
        """baostock 无主力资金流接口,返回 None,筛选器会跳过 L8 层"""
        return None

    # -------------------- L8.5: baostock 不支持 --------------------
    def get_lhb_institution_flow(
        self, symbol: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """baostock 无龙虎榜接口,返回 None,筛选器会跳过 L8.5 层"""
        return None

    # -------------------- 个股基本信息 --------------------
    def get_stock_info(self, symbol: str) -> Optional[dict]:
        key = f"info_bs_{symbol}"
        cached = self._read_cache(key)
        if cached is not None and not cached.empty:
            return dict(zip(cached["item"], cached["value"]))

        try:
            ensure_logged_in()
            bs_code = to_bs_code(symbol)
            rs = self._retry(bs.query_stock_basic, code=bs_code)
            df = self._rs_to_df(rs)
            if df.empty:
                return None
            row = df.iloc[0].to_dict()
            kv = pd.DataFrame(list(row.items()), columns=["item", "value"])
            self._write_cache(key, kv)
            return row
        except Exception as e:
            logger.debug(f"{symbol} 基本信息获取失败: {e}")
            return None

    # -------------------- 大盘指数 --------------------
    def get_index(self, symbol: str = "sh.000300", days: int = 60,
                  end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        bs_code = to_bs_index(symbol)
        end = end_date or datetime.now(TZ).strftime("%Y%m%d")
        end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
        start_fmt = (datetime.strptime(end, "%Y%m%d") - timedelta(days=days * 2)).strftime("%Y-%m-%d")

        key = f"index_bs_{bs_code}_{start_fmt}_{end_fmt}"
        cached = self._read_cache(key)
        if cached is not None:
            return cached.tail(days).reset_index(drop=True)

        try:
            ensure_logged_in()
            rs = self._retry(
                bs.query_history_k_data_plus,
                bs_code,
                "date,open,high,low,close,volume,amount,pctChg",
                start_date=start_fmt,
                end_date=end_fmt,
                frequency="d",
            )
            df = self._rs_to_df(rs)
            if df.empty:
                return None
            df["date"] = pd.to_datetime(df["date"])
            for col in ["open", "high", "low", "close", "amount", "pctChg"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
            df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
            self._write_cache(key, df)
            return df.tail(days).reset_index(drop=True)
        except Exception as e:
            logger.warning(f"指数 {symbol} 获取失败: {e}")
            return None

    # -------------------- 全市场股票列表 --------------------
    def get_all_stocks(self, query_date: Optional[str] = None,
                       max_lookback: int = 10) -> Optional[pd.DataFrame]:
        """
        baostock 用 query_all_stock(date) 返回当日全部交易标的
        无总市值/流通市值字段

        关键修复:
          - baostock 数据延迟 1 天,且周末/节假日返回空
          - 自动向前回退最多 max_lookback 个自然日,直到找到有效交易日
        """
        # 默认从昨天开始查(baostock 数据有 1 天延迟)
        if query_date:
            base_date = datetime.strptime(query_date, "%Y-%m-%d")
        else:
            base_date = datetime.now(TZ) - timedelta(days=1)

        for offset in range(max_lookback):
            try_date = (base_date - timedelta(days=offset)).strftime("%Y-%m-%d")
            key = f"all_stocks_bs_{try_date}"
            cached = self._read_cache(key)
            if cached is not None and not cached.empty:
                logger.info(f"全市场列表使用缓存日期 {try_date} ({len(cached)} 只)")
                return cached

            try:
                ensure_logged_in()
                rs = self._retry(bs.query_all_stock, day=try_date)
                df = self._rs_to_df(rs)
                if df.empty:
                    logger.debug(f"{try_date} 无数据(可能非交易日),回退")
                    continue

                df = df.rename(columns={
                    "code": "bs_code",
                    "code_name": "name",
                    "tradeStatus": "trade_status",
                })
                df["symbol"] = df["bs_code"].apply(from_bs_code)
                # 只保留正常交易
                df = df[df["trade_status"] == "1"].copy()
                # 排除指数(sh.000xxx / sz.399xxx)和北交所
                df = df[~df["bs_code"].str.startswith(("sh.000", "sz.399", "bj."))]

                if df.empty:
                    logger.debug(f"{try_date} 过滤后为空,回退")
                    continue

                logger.info(f"全市场列表查询日期 {try_date},共 {len(df)} 只")
                self._write_cache(key, df)
                return df
            except Exception as e:
                logger.debug(f"{try_date} 查询异常: {e},回退")
                continue

        logger.error(f"近 {max_lookback} 个自然日均无法获取全市场列表")
        return None
