"""
龙头断板筛选器 — 9 层识别
================================================
与主升前夜策略共享底座,但识别逻辑完全独立。

L1  涨停过滤        — 近 20 日至少 1 次涨停
L2  连板高度        — 近 10 日内曾出现 ≥2 连板
L3  连板累计涨幅    — 最近连板段累计涨幅 ≥ 15%
L4  板块同步度      — 连板期间有 ≥3 只票同日涨停
L5  有效断板        — 最近 3 日内出现"涨停→断板"形态
L6  断板日放量      — 断板日量 ≥ 前日 1.5 倍
L7  MACD 动能未死   — DIF > DEA
L8  非炸板          — 近 3 日无一字跌停
L9  风控/大盘       — 非 ST/上市够久/大盘健康

买点: 断板次日开盘 ≤ 断板日收盘价的 97% 挂单
止损: 买入价 × 0.95 (5%)
持仓: 最多 5 日
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from config import DragonConfig, TZ
from data_loader import DataLoader
from indicators import (
    macd, macd_resonance, sma, volume_ratio,
    count_consecutive_limit_ups,
    max_consecutive_limit_ups_in_window,
    find_break_board_days,
    has_one_word_crash,
)

logger = logging.getLogger(__name__)


# ============================================================
@dataclass
class DragonSignal:
    symbol: str
    name: str
    eval_date: str
    layers_passed: dict = field(default_factory=dict)
    layer_details: dict = field(default_factory=dict)
    score: int = 0
    triggered: bool = False
    notes: list = field(default_factory=list)

    # 交易参数
    last_close: float = 0.0
    break_board_date: str = ""
    break_board_close: float = 0.0
    suggested_entry: float = 0.0    # 次日挂单价
    suggested_stop: float = 0.0     # 止损价

    def fail(self, layer: str, reason: str) -> "DragonSignal":
        self.layers_passed[layer] = False
        self.layer_details[layer] = reason
        self.notes.append(f"{layer}: {reason}")
        return self

    def ok(self, layer: str, detail: str = "") -> "DragonSignal":
        self.layers_passed[layer] = True
        self.layer_details[layer] = detail or "pass"
        self.score += 1
        return self


# ============================================================
def detect_limit_pct(symbol: str, name: str, cfg: DragonConfig) -> float:
    """涨停阈值判定(支持主板 10%/创业板 20%/ST 5%)"""
    name_upper = (name or "").upper()
    if "ST" in name_upper or "*" in name_upper:
        return 0.048
    if symbol.startswith(("300", "688", "301")):
        return cfg.limit_up_pct_chinext
    return cfg.limit_up_pct_main


# ============================================================
class DragonScreener:
    """
    龙头断板筛选器
    与 PreMainUptrendScreener 共享 DataLoader 接口,独立识别逻辑
    """

    def __init__(self, cfg: Optional[DragonConfig] = None,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg or DragonConfig()
        self.loader = loader or DataLoader()
        self._index_health_cache: Optional[bool] = None
        self._index_check_date: Optional[str] = None
        self._sector_sync_cache: Optional[dict] = None
        self._sector_check_date: Optional[str] = None

    # -------------------- 大盘健康度 --------------------
    def _check_index_health(self, end_date: Optional[str] = None) -> bool:
        today = end_date or datetime.now(TZ).strftime("%Y-%m-%d")
        if self._index_health_cache is not None and self._index_check_date == today:
            return self._index_health_cache

        df = self.loader.get_index(self.cfg.index_symbol, days=60, end_date=end_date)
        if df is None or len(df) < self.cfg.index_ma + 5:
            healthy = True  # 数据缺失时默认放行
        else:
            ma = df["close"].rolling(self.cfg.index_ma).mean()
            last_close = df["close"].iloc[-1]
            ma_now, ma_prev = ma.iloc[-1], ma.iloc[-5]
            healthy = bool(last_close > ma_now or ma_now > ma_prev)
        self._index_health_cache = healthy
        self._index_check_date = today
        return healthy

    # -------------------- 板块同步度(隐式聚类) --------------------
    def _compute_sector_sync(self, symbols_hint: Optional[list] = None,
                               end_date: Optional[str] = None) -> dict:
        """
        遍历一组股票的近期数据,统计"同一天有多少只股票涨停"
        返回 {date_str: count},用于 L4 判断

        为了避免全市场扫描过慢,可传入 symbols_hint 限制范围
        默认使用随机抽样的 sector_sync_sample_size 只
        """
        today = end_date or datetime.now(TZ).strftime("%Y-%m-%d")
        if self._sector_sync_cache is not None and self._sector_check_date == today:
            return self._sector_sync_cache

        # 这里简化处理: 返回空 dict,让 L4 在没有板块数据时默认放行
        # 真实实现需要在 scan 之前批量拉数据计算
        # 见 scan_universe 中的预计算逻辑
        self._sector_sync_cache = {}
        self._sector_check_date = today
        return self._sector_sync_cache

    def set_sector_sync(self, sync_map: dict, eval_date: str) -> None:
        """外部注入预计算的板块同步度"""
        self._sector_sync_cache = sync_map
        self._sector_check_date = eval_date

    # -------------------- 评估单只股票 --------------------
    def evaluate(self, symbol: str, name: str = "",
                 end_date: Optional[str] = None) -> DragonSignal:
        # 拉 90 个交易日 (足够 L1-L8 用)
        df = self.loader.get_kline(symbol, days=90, end_date=end_date)
        return self._evaluate_core(symbol, name, df, end_date)
    
    def evaluate_with_data(self, symbol: str, name: str, df: pd.DataFrame,
                           end_date: Optional[str] = None) -> DragonSignal:
        """
        评估单只股票 (使用已提供的 K 线数据)
        
        优化：避免重复调用 get_kline，用于预取数据场景
        """
        return self._evaluate_core(symbol, name, df, end_date)
    
    def _evaluate_core(self, symbol: str, name: str, df: Optional[pd.DataFrame],
                       end_date: Optional[str] = None) -> DragonSignal:
        sig = DragonSignal(
            symbol=symbol, name=name,
            eval_date=end_date or datetime.now(TZ).strftime("%Y-%m-%d"),
        )

        c = self.cfg

        if df is None or len(df) < c.listing_days_min:
            return sig.fail("L0",
                f"数据不足({len(df) if df is not None else 0} 日，需 {c.listing_days_min})")

        last = df.iloc[-1]
        sig.last_close = float(last["close"])
        limit_pct = detect_limit_pct(symbol, name, c)

        # ---------- L1 涨停过滤 ----------
        pct = df["close"].pct_change()
        recent_pct = pct.tail(c.limit_up_lookback)
        limit_up_days = int((recent_pct >= limit_pct - 0.003).sum())
        if limit_up_days == 0:
            return sig.fail("L1", f"近 {c.limit_up_lookback} 日无涨停")
        sig.ok("L1", f"{limit_up_days} 次涨停")

        # ---------- L2 连板高度 ----------
        max_run = max_consecutive_limit_ups_in_window(
            df, limit_pct, c.consecutive_lookback
        )
        if max_run < c.consecutive_limit_up_min:
            return sig.fail("L2", f"最大连板仅 {max_run} 板")
        sig.ok("L2", f"最大 {max_run} 连板")

        # ---------- L3 连板期间累计涨幅 ----------
        # 近 10 日的累计涨幅(用 close 首尾比)
        start_close = df["close"].iloc[-c.consecutive_lookback - 1] \
            if len(df) > c.consecutive_lookback else df["close"].iloc[0]
        end_close = float(last["close"])
        cumulative = (end_close - start_close) / start_close if start_close > 0 else 0
        if cumulative < c.cumulative_gain_min:
            return sig.fail("L3", f"近 {c.consecutive_lookback} 日累涨仅 {cumulative:.1%}")
        sig.ok("L3", f"累涨 {cumulative:.1%}")

        # ---------- L4 板块同步度 ----------
        # 如果没有预计算的同步度数据,这一层默认放行但不计分
        sync_map = self._sector_sync_cache or {}
        if not sync_map:
            sig.notes.append("L4: 板块同步度数据未预计算, 跳过(不计分)")
            sig.layer_details["L4"] = "sync_not_precomputed"
        else:
            # 近 5 日内是否有"同日涨停数 ≥ 3"
            recent_dates = df.tail(c.sector_sync_lookback)["date"]
            max_sync = 0
            for d in recent_dates:
                d_key = pd.Timestamp(d).strftime("%Y-%m-%d")
                count = sync_map.get(d_key, 0)
                max_sync = max(max_sync, count)
            if max_sync < c.sector_sync_min_count:
                return sig.fail("L4", f"近 5 日同步涨停峰值仅 {max_sync} 只")
            sig.ok("L4", f"同步涨停 {max_sync} 只")

        # ---------- L5 有效断板 ----------
        breaks = find_break_board_days(
            df, limit_pct,
            min_pct=c.break_board_min_pct,
            max_pct=c.break_board_max_pct,
            lookback=c.break_board_lookback,
        )
        if not breaks:
            return sig.fail("L5", f"近 {c.break_board_lookback} 日无有效断板")
        # 取最近一次断板
        break_day = breaks[0]
        sig.break_board_date = pd.Timestamp(break_day["date"]).strftime("%Y-%m-%d")
        sig.break_board_close = break_day["close"]
        sig.ok("L5", f"{sig.break_board_date} 断板 ({break_day['pct_change']:+.1%})")

        # ---------- L6 断板日放量 ----------
        if break_day["prev_volume"] > 0:
            vol_ratio = break_day["volume"] / break_day["prev_volume"]
            if vol_ratio < c.break_volume_ratio:
                return sig.fail("L6", f"断板日量比仅 {vol_ratio:.2f}")
            sig.ok("L6", f"断板日量比 {vol_ratio:.2f}")
        else:
            return sig.fail("L6", "前日成交量异常")

        # ---------- L7 MACD 动能 ----------
        if len(df) >= 30:
            m = macd(df["close"], c.macd_fast, c.macd_slow, c.macd_signal)
            dif = m["DIF"].iloc[-1]
            dea = m["DEA"].iloc[-1]
            if dif <= dea:
                return sig.fail("L7", f"MACD 死叉 DIF={dif:.3f} DEA={dea:.3f}")
            sig.ok("L7", f"DIF>DEA ({dif:.3f}>{dea:.3f})")
        else:
            sig.notes.append("L7: 数据不足 30 日,MACD 跳过")
            sig.layer_details["L7"] = "insufficient_data_skipped"

        # ---------- L8 非炸板 ----------
        if has_one_word_crash(df, c.crash_lookback, c.one_word_down_threshold):
            return sig.fail("L8", "近 3 日出现一字跌停(炸板)")
        sig.ok("L8", "无炸板")

        # ---------- L9 风控/大盘 ----------
        name_upper = (name or "").upper()
        if "ST" in name_upper or "*" in name_upper:
            return sig.fail("L9", "ST/风险股")
        if len(df) < c.listing_days_min:
            return sig.fail("L9", f"上市仅 {len(df)} 日")
        if not self._check_index_health(end_date):
            return sig.fail("L9", "大盘(沪深300)处于破位状态")
        sig.ok("L9", "风控通过")

        # ---------- 综合判定 ----------
        sig.triggered = sig.score >= c.min_layers_to_trigger

        # 给出建议挂单价和止损
        if sig.triggered:
            # 次日开盘挂单价: 断板日收盘 × 0.97
            sig.suggested_entry = round(sig.break_board_close * 0.97, 2)
            # 止损: 挂单价 × 0.95
            sig.suggested_stop = round(sig.suggested_entry * (1 - c.stop_loss_pct), 2)

        return sig


# ============================================================
def precompute_sector_sync(
    symbols: list[tuple[str, str]],
    loader: DataLoader,
    cfg: Optional[DragonConfig] = None,
    days: int = 30,
    end_date: Optional[str] = None,
) -> dict:
    """
    预计算板块同步度：统计每个交易日有多少只股票涨停
    
    用于 L4 层判断，支持缓存复用 (避免每天全量重算)
    """
    cfg = cfg or DragonConfig()
    sync_map: dict = {}
    
    for i, (code, name) in enumerate(symbols):
        df = loader.get_kline(code, days=days, end_date=end_date)
        if df is None or len(df) < 10:
            continue
        
        limit_pct = detect_limit_pct(code, name, cfg)
        
        # 计算涨跌幅（与前一天相比）
        df_with_pct = df.copy()
        df_with_pct["pct"] = df_with_pct["close"].pct_change()
        
        # 只看最近 sector_sync_lookback 天
        lookback_df = df_with_pct.tail(cfg.sector_sync_lookback)
        
        for _, row in lookback_df.iterrows():
            p = row["pct"]
            if p is not None and not pd.isna(p) and p >= limit_pct - 0.003:
                d_key = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
                sync_map[d_key] = sync_map.get(d_key, 0) + 1
    
    return sync_map


def scan_universe_dragon(
    symbols: list[tuple[str, str]],
    cfg: Optional[DragonConfig] = None,
    loader: Optional[DataLoader] = None,
    end_date: Optional[str] = None,
    progress_every: int = 100,
    skip_insufficient_data: bool = True,
    sector_sync: Optional[dict] = None,  # 外部传入的板块同步度
    eval_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    批量扫描龙头断板信号
    
    支持两种模式:
    1. 自动模式 (sector_sync=None): 内部预计算板块同步度
    2. 注入模式 (sector_sync=dict): 使用外部预计算的同步度 (用于 main.py 的抽样优化)
    """
    cfg = cfg or DragonConfig()
    loader = loader or DataLoader()
    screener = DragonScreener(cfg, loader)

    # ========== 第一轮:预计算板块同步度 ==========
    if sector_sync is None:
        # 自动模式：内部预计算
        logger.info(f"预扫描 {min(len(symbols), cfg.sector_sync_sample_size)} 只，统计板块同步度")
        import random
        sample = symbols[:cfg.sector_sync_sample_size] \
            if len(symbols) > cfg.sector_sync_sample_size else symbols
        sync_map: dict = {}
        for i, (code, name) in enumerate(sample):
            df = loader.get_kline(code, days=30, end_date=end_date)
            if df is None or len(df) < 10:
                continue
            limit_pct = detect_limit_pct(code, name, cfg)
            pct = df["close"].pct_change()
            for j, row in df.tail(cfg.sector_sync_lookback + 5).iterrows():
                if j == 0:
                    continue
                p = pct.iloc[j] if j < len(pct) else None
                if p is not None and not pd.isna(p) and p >= limit_pct - 0.003:
                    d_key = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
                    sync_map[d_key] = sync_map.get(d_key, 0) + 1
            if (i + 1) % 100 == 0:
                logger.info(f"  预扫描 {i+1}/{len(sample)}")

        logger.info(f"板块同步度预计算完成，涵盖 {len(sync_map)} 个交易日")
        if sync_map:
            top = sorted(sync_map.items(), key=lambda x: -x[1])[:5]
            logger.info(f"同步涨停数前 5: {top}")
    else:
        # 注入模式：使用外部预计算的同步度
        sync_map = sector_sync
        logger.info(f"使用外部注入的板块同步度，涵盖 {len(sync_map)} 个交易日")
        if sync_map:
            top = sorted(sync_map.items(), key=lambda x: -x[1])[:5]
            logger.info(f"同步涨停数前 5: {top}")

    # ========== 第二轮:正式扫描 ==========
    rows = []
    skipped_no_data = 0
    eval_date_str = eval_date or end_date or datetime.now(TZ).strftime("%Y-%m-%d")
    screener.set_sector_sync(sync_map, eval_date_str)

    for i, (code, name) in enumerate(symbols, 1):
        try:
            sig = screener.evaluate(code, name, end_date=end_date)
            if skip_insufficient_data and sig.layer_details.get("L0", "").startswith("数据不足"):
                skipped_no_data += 1
                continue
            row = {
                "symbol": code, "name": name,
                "score": sig.score,
                "triggered": sig.triggered,
                "last_close": sig.last_close,
                "break_board_date": sig.break_board_date,
                "break_board_close": sig.break_board_close,
                "suggested_entry": sig.suggested_entry,
                "suggested_stop": sig.suggested_stop,
            }
            for k, v in sig.layers_passed.items():
                row[k] = "✓" if v else "✗"
            row["last_fail"] = sig.notes[-1] if sig.notes else ""
            rows.append(row)
        except Exception as e:
            logger.error(f"{code} 龙头断板评估异常: {e}")
            rows.append({"symbol": code, "name": name, "error": str(e)})

        if i % progress_every == 0:
            triggered = sum(1 for r in rows if r.get("triggered"))
            logger.info(f"龙头断板扫描 {i}/{len(symbols)}, 跳过 {skipped_no_data}, "
                        f"有效 {len(rows)}, 触发 {triggered}")

    df = pd.DataFrame(rows)
    if "triggered" in df.columns and "score" in df.columns:
        df = df.sort_values(["triggered", "score"], ascending=[False, False])
    return df.reset_index(drop=True)
