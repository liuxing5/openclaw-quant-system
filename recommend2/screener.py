"""
主升前夜筛选器 — 12 层过滤
================================================
对原"四特征"主观总结的量化重构

L1   底部定义
L2   低位涨停痕迹
L3   有效跳空缺口
L4   连阳质量
L5   突破日倍量
L6   量能持续
L7   MACD 共振 (8/17/9)
L8   主力资金流入(同花顺/东财口径)
L8.5 龙虎榜机构席位净买入 (新增)
L9   剔除高位
L10  风控过滤
L11  大盘环境
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from config import ScreenerConfig, TZ
from data_loader import DataLoader
from indicators import (
    macd, macd_resonance, sma, volume_ratio,
    detect_limit_up_pct, find_limit_up_days,
    find_unfilled_gaps, longest_yang_run,
)

logger = logging.getLogger(__name__)


# ============================================================
@dataclass
class StockSignal:
    symbol: str
    name: str
    eval_date: str
    layers_passed: dict = field(default_factory=dict)
    layer_details: dict = field(default_factory=dict)
    score: int = 0
    triggered: bool = False
    notes: list = field(default_factory=list)
    last_close: float = 0.0
    suggested_stop: float = 0.0
    suggested_position_pct: float = 0.0

    def fail(self, layer: str, reason: str) -> "StockSignal":
        self.layers_passed[layer] = False
        self.layer_details[layer] = reason
        self.notes.append(f"{layer}: {reason}")
        return self

    def ok(self, layer: str, detail: str = "") -> "StockSignal":
        self.layers_passed[layer] = True
        self.layer_details[layer] = detail or "pass"
        self.score += 1
        return self


# ============================================================
class PreMainUptrendScreener:
    def __init__(self, cfg: Optional[ScreenerConfig] = None,
                 loader: Optional[DataLoader] = None):
        self.cfg = cfg or ScreenerConfig()
        self.loader = loader or DataLoader()
        self._index_health_cache: Optional[bool] = None
        self._index_check_date: Optional[str] = None

    # -------------------- 大盘健康度(全局缓存) --------------------
    def _check_index_health(self) -> bool:
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        if self._index_health_cache is not None and self._index_check_date == today:
            return self._index_health_cache

        df = self.loader.get_index(self.cfg.index_symbol, days=60)
        if df is None or len(df) < self.cfg.index_ma + 5:
            logger.warning("指数数据不足,默认放行")
            healthy = True
        else:
            ma = df["close"].rolling(self.cfg.index_ma).mean()
            last_close = df["close"].iloc[-1]
            ma_now, ma_prev = ma.iloc[-1], ma.iloc[-5]
            healthy = bool(last_close > ma_now or ma_now > ma_prev)
        self._index_health_cache = healthy
        self._index_check_date = today
        return healthy

    # -------------------- 评估单只股票 --------------------
    def evaluate(self, symbol: str, name: str = "",
                 end_date: Optional[str] = None) -> StockSignal:
        sig = StockSignal(
            symbol=symbol, name=name,
            eval_date=end_date or datetime.now(TZ).strftime("%Y-%m-%d"),
        )

        # ---------- 数据准备 ----------
        c = self.cfg

        # 拉 600 个交易日数据(够 L1 的 500 日高点窗口 + 100 日缓冲)
        df = self.loader.get_kline(symbol, days=600, end_date=end_date)
        if df is None or len(df) < c.high_lookback_days:
            return sig.fail("L0",
                f"数据不足 (需 {c.high_lookback_days} 行, 实际 {len(df) if df is not None else 0})")

        last = df.iloc[-1]
        sig.last_close = float(last["close"])

        # ---------- L1 底部定义 ----------
        # 用更长窗口找真正的历史高点(避免错过深度回撤的大底机会)
        h_lookback = c.high_lookback_days
        l_lookback = c.low_lookback_days
        h_max = df["high"].tail(h_lookback).max()
        l_min = df["low"].tail(l_lookback).min()
        drawdown = (h_max - last["close"]) / h_max if h_max > 0 else 0
        rebound = (last["close"] - l_min) / l_min if l_min > 0 else 0
        if drawdown < c.bottom_drawdown_min:
            return sig.fail("L1", f"距 {h_lookback}日高点回撤仅 {drawdown:.1%}")
        if rebound > c.bottom_rebound_max:
            return sig.fail("L1", f"距 {l_lookback}日低点已反弹 {rebound:.1%}")
        sig.ok("L1", f"DD={drawdown:.1%}, RB={rebound:.1%}")

        # ---------- L2 低位涨停痕迹 ----------
        limit_pct = detect_limit_up_pct(symbol, name)
        recent = df.tail(c.limit_up_lookback)
        lu_days = find_limit_up_days(recent, limit_pct)
        valid = False
        for _, row in lu_days.iterrows():
            zone = (row["close"] - l_min) / l_min if l_min > 0 else 99
            if zone <= c.limit_up_low_zone:
                valid = True
                break
        if not valid:
            return sig.fail("L2", f"近 {c.limit_up_lookback} 日无低位涨停")
        sig.ok("L2", f"涨停阈值 {limit_pct:.1%}")

        # ---------- L3 有效跳空缺口 ----------
        gaps = find_unfilled_gaps(
            df, c.gap_min_pct, c.gap_no_fill_days,
            c.gap_volume_ratio, c.gap_search_window
        )
        if not gaps:
            return sig.fail("L3", "无有效未回补缺口")
        sig.ok("L3", f"{len(gaps)} 个缺口, 最大 {max(g['gap_pct'] for g in gaps):.1%}")

        # ---------- L4 连阳质量 ----------
        run, broke = longest_yang_run(
            df, c.yang_search_window, c.yang_no_break_ma5
        )
        if run < c.consecutive_yang_min:
            return sig.fail("L4", f"最长连阳仅 {run} 日")
        if broke:
            return sig.fail("L4", "连阳期间破 5MA")
        sig.ok("L4", f"连阳 {run} 日")

        # ---------- L5 突破日倍量 ----------
        ma_vol20 = df["volume"].rolling(20).mean().iloc[-1]
        if pd.isna(ma_vol20) or ma_vol20 == 0:
            return sig.fail("L5", "20 日均量缺失")
        breakout_ratio = last["volume"] / ma_vol20
        if breakout_ratio < c.breakout_volume_mult:
            return sig.fail("L5", f"突破日量比 {breakout_ratio:.2f}")
        sig.ok("L5", f"量比 {breakout_ratio:.2f}")

        # ---------- L6 量能持续 ----------
        last_n = df.tail(c.sustain_window)
        sustain_count = int((last_n["volume"] >= ma_vol20 * c.sustain_volume_mult).sum())
        if sustain_count < c.sustain_days_required:
            return sig.fail("L6", f"近 {c.sustain_window} 日仅 {sustain_count} 日放量")
        sig.ok("L6", f"{sustain_count}/{c.sustain_window} 日放量")

        # ---------- L7 MACD(8/17/9) 共振 ----------
        m = macd(df["close"], c.macd_fast, c.macd_slow, c.macd_signal)
        resonant, kind = macd_resonance(m)
        if not resonant:
            return sig.fail("L7", "MACD 未共振")
        sig.ok("L7", f"MACD {kind}")

        # ---------- L8 主力资金流入 ----------
        mf = self.loader.get_money_flow(symbol)
        if mf is None or mf.empty:
            if c.allow_l8_missing:
                sig.notes.append("L8: 资金流缺失,跳过(不计分)")
                sig.layer_details["L8"] = "data_missing_skipped"
            else:
                return sig.fail("L8", "资金流数据缺失")
        else:
            last5_net = float(mf.tail(5)["main_net"].sum())
            today_net = float(mf.iloc[-1]["main_net"])
            if last5_net <= 0 or today_net <= 0:
                return sig.fail("L8", f"主力 5 日净额 {last5_net/1e8:.2f} 亿")
            sig.ok("L8", f"5 日 {last5_net/1e8:.2f} 亿")

        # ---------- L8.5 龙虎榜机构席位 ----------
        eval_dt = datetime.strptime(sig.eval_date, "%Y-%m-%d")
        lhb_start = (eval_dt - timedelta(days=c.lhb_lookback_days)).strftime("%Y%m%d")
        lhb_end = eval_dt.strftime("%Y%m%d")
        lhb = self.loader.get_lhb_institution_flow(symbol, lhb_start, lhb_end)

        if lhb is None:
            # 接口异常
            if c.allow_lhb_missing:
                sig.notes.append("L8.5: 龙虎榜接口异常,跳过(不计分)")
                sig.layer_details["L8.5"] = "data_error_skipped"
            else:
                return sig.fail("L8.5", "龙虎榜数据接口异常")
        elif lhb.empty:
            # 近 30 日未上榜 — 中性事件,不算失败但也不计分
            if c.lhb_required:
                return sig.fail("L8.5", "近 30 日未上榜")
            else:
                sig.notes.append("L8.5: 近 30 日未上榜(中性)")
                sig.layer_details["L8.5"] = "no_listing_neutral"
        else:
            # 有上榜记录: 检查机构席位累计净买
            inst_net_total = float(lhb["inst_net"].sum())
            if inst_net_total <= c.lhb_inst_net_min:
                return sig.fail("L8.5",
                    f"机构席位 {len(lhb)} 次上榜净买 {inst_net_total/1e8:.2f} 亿")
            sig.ok("L8.5",
                f"{len(lhb)} 次上榜机构净买 {inst_net_total/1e8:.2f} 亿")

        # ---------- L9 剔除高位 ----------
        ma60 = df["close"].rolling(60).mean().iloc[-1]
        if pd.isna(ma60):
            return sig.fail("L9", "60MA 缺失")
        above_ma60 = (last["close"] - ma60) / ma60
        if above_ma60 > c.above_ma60_max:
            return sig.fail("L9", f"已高于 60MA {above_ma60:.1%}")
        sig.ok("L9", f"高于 60MA {above_ma60:.1%}")

        # ---------- L10 风控过滤 ----------
        name_upper = (name or "").upper()
        if "ST" in name_upper or "*" in name_upper:
            return sig.fail("L10", "ST/风险股")
        if len(df) < c.listing_days_min:
            return sig.fail("L10", f"上市仅 {len(df)} 日")
        sig.ok("L10")

        # ---------- L11 大盘环境 ----------
        if not self._check_index_health():
            return sig.fail("L11", "沪深 300 破位")
        sig.ok("L11")

        # ---------- 综合判定 ----------
        sig.triggered = sig.score >= c.min_layers_to_trigger

        # 给出建议止损价(2% 风控规则)
        if sig.triggered:
            sig.suggested_stop = sig.last_close * (1 - 0.08)  # 单票最大 8% 跌幅容忍
            sig.suggested_position_pct = 0.02 / 0.08          # ≈ 25% 单仓上限
        return sig


# ============================================================
def scan_universe(symbols: list[tuple[str, str]],
                  cfg: Optional[ScreenerConfig] = None,
                  loader: Optional[DataLoader] = None,
                  end_date: Optional[str] = None,
                  progress_every: int = 50,
                  skip_insufficient_data: bool = True) -> pd.DataFrame:
    """
    批量扫描
    symbols: [(code, name), ...]
    skip_insufficient_data: 如果为 True,数据不足的票直接跳过(不进 result),
        避免 L0 失败刷屏淹没真实信号
    返回按得分降序的 DataFrame
    """
    screener = PreMainUptrendScreener(cfg, loader)
    rows = []
    skipped_no_data = 0
    for i, (code, name) in enumerate(symbols, 1):
        try:
            sig = screener.evaluate(code, name, end_date=end_date)
            # 跳过数据不足的票(通常是新股/退市/ETF 漏网之鱼)
            if skip_insufficient_data and sig.layer_details.get("L0", "").startswith("数据不足"):
                skipped_no_data += 1
                continue
            row = {
                "symbol": code, "name": name,
                "score": sig.score,
                "triggered": sig.triggered,
                "last_close": sig.last_close,
                "suggested_stop": round(sig.suggested_stop, 2),
            }
            for k, v in sig.layers_passed.items():
                row[k] = "✓" if v else "✗"
            row["last_fail"] = sig.notes[-1] if sig.notes else ""
            rows.append(row)
        except Exception as e:
            logger.error(f"{code} 评估异常: {e}")
            rows.append({"symbol": code, "name": name, "error": str(e)})

        if i % progress_every == 0:
            triggered = sum(1 for r in rows if r.get("triggered"))
            logger.info(f"已扫描 {i}/{len(symbols)}, 数据不足跳过 {skipped_no_data} 只, "
                        f"有效 {len(rows)} 只, 触发 {triggered} 只")

    df = pd.DataFrame(rows)
    if "triggered" in df.columns and "score" in df.columns:
        df = df.sort_values(["triggered", "score"], ascending=[False, False])
    return df.reset_index(drop=True)
