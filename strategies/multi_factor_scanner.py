"""
multi_factor_scanner.py  v4.0
==============================
A股多因子短期隔夜推荐扫描器

数据源（经网络诊断验证可用）：
  · 股票池+行情  : 腾讯批量行情 qt.gtimg.cn（100只/次，<1s）
  · 日线K线      : ak.stock_zh_a_hist_tx（腾讯，~1.8s/只）
  · 备用日线     : ak.stock_zh_a_daily（新浪，~1.6s/只）

指标：截面动量 / 量价背离 / RSI / MACD / 均线 / DMI-ADX / SAR
注意：腾讯K线无volume字段，用amount（成交额）代替，量比逻辑不变

版本：4.0  2026-05
"""

import os
for _k in ["HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"]:
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings, time, sys, threading, requests

warnings.filterwarnings("ignore")

_session = requests.Session()
_session.trust_env = False
_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.qq.com"}

# ============================================================
# ======================== 配置 ==============================
# ============================================================
CFG = {
    "max_workers"          : 12,      # 腾讯K线并发数（实测1.8s/只，12线程≈900只/2min）
    "batch_size"           : 100,     # 腾讯批量行情每批数量上限

    # K线
    "n_days"               : 60,      # 取最近N个交易日
    "min_valid_days"       : 30,

    # 预筛（腾讯实时行情字段）
    "pre_min_price"        : 3.0,
    "pre_max_price"        : 300.0,
    "pre_min_turnover"     : 0.3,     # 换手率% 下限
    "pre_max_turnover"     : 20.0,    # 换手率% 上限
    "pre_min_pct"          : -5.0,    # 涨跌幅% 下限
    "pre_max_pct"          : 9.4,     # 涨停排除
    "pre_min_amount"       : 5000,    # 成交量（手）下限，过滤流动性极差
    "exclude_st"           : True,

    # 因子参数
    "momentum_skip"        : 1,
    "momentum_window"      : 20,
    "momentum_threshold"   : 0.05,
    "vol_ratio_window"     : 20,      # 量比窗口（用amount代替volume）
    "vol_ratio_min"        : 1.5,
    "vol_ratio_max"        : 10.0,
    "pv_corr_window"       : 10,
    "rsi_period"           : 6,
    "rsi_oversold"         : 35,
    "rsi_overbought"       : 75,
    "macd_fast"            : 12,
    "macd_slow"            : 26,
    "macd_signal"          : 9,
    "ema_short"            : 5,
    "ema_mid"              : 10,
    "ema_long"             : 20,
    "adx_period"           : 14,
    "adx_threshold"        : 20,
    "sar_af_init"          : 0.02,
    "sar_af_max"           : 0.2,

    # 权重
    "weight_momentum"      : 0.20,
    "weight_volume"        : 0.20,
    "weight_rsi"           : 0.15,
    "weight_macd"          : 0.15,
    "weight_ema"           : 0.15,
    "weight_adx"           : 0.10,
    "weight_sar"           : 0.05,

    "top_n"                : 20,
    "output_csv"           : True,
}

# ============================================================
# ==================== 腾讯行情字段说明 =====================
# v_sh600519="1~名称~代码~现价~昨收~今开~成交量(手)~外盘~内盘~买一~...
# idx:  0=类型 1=名称 2=代码 3=现价 4=昨收 5=今开 6=成交量(手)
#       31=涨跌幅% 32=涨跌额 38=换手率% 37=振幅 39=最高 40=最低
#       44=成交额(万元) 47=流通市值 48=总市值
# ============================================================

def parse_qq_quote(line: str) -> dict | None:
    """解析腾讯行情单行"""
    try:
        # 格式: v_sh600519="1~贵州茅台~600519~..."
        content = line.split('"')[1]
        p = content.split('~')
        if len(p) < 45:
            return None
        code = p[2].zfill(6)
        name = p[1]
        price = float(p[3]) if p[3] else 0
        if price <= 0:
            return None
        return {
            "code"    : code,
            "name"    : name,
            "price"   : price,
            "yclose"  : float(p[4]) if p[4] else price,
            "open"    : float(p[5]) if p[5] else price,
            "volume"  : float(p[6]) if p[6] else 0,     # 成交量（手）
            "pct_chg" : float(p[31]) if p[31] else 0,   # 涨跌幅%
            "turnover": float(p[38]) if p[38] else 0,   # 换手率%
            "amount"  : float(p[44]) if p[44] else 0,   # 成交额（万元）
        }
    except Exception:
        return None


def get_qq_batch(symbols: list[str]) -> list[dict]:
    """
    腾讯批量行情，symbols格式: ['sh600519','sz000001',...]
    返回解析后的行情列表
    """
    query = ",".join(symbols)
    try:
        r = _session.get(f"https://qt.gtimg.cn/q={query}",
                         timeout=8, headers=_HDR)
        r.raise_for_status()
        rows = []
        for line in r.text.strip().split('\n'):
            if '~' not in line:
                continue
            rec = parse_qq_quote(line)
            if rec:
                rows.append(rec)
        return rows
    except Exception:
        return []


# ============================================================
# ==================== 步骤1：股票池 + 行情 ==================
# ============================================================

# 腾讯代码格式映射
def to_qq_symbol(code: str) -> str:
    """600xxx/688xxx/5xxxxx → sh前缀，其余 → sz前缀"""
    c = code.zfill(6)
    return ("sh" if c[0] in ("6", "5") else "sz") + c


def get_all_codes() -> list[str]:
    """
    获取全市场A股代码（排除北交所）。
    用腾讯沪深市场列表接口，备用 AKShare stock_info_a_code_name。
    """
    # 方法1：构造沪市(sh) + 深市(sz) 代码范围
    # 沪市主板: 600000-603999, 605000-606999
    # 沪市科创: 688000-689999
    # 深市主板: 000001-001999
    # 深市中小: 002000-004999
    # 深市创业: 300000-301999
    sh_ranges = list(range(600000, 604000)) + list(range(605000, 607000)) + list(range(688000, 690000))
    sz_ranges = list(range(1, 2000)) + list(range(2000, 5000)) + list(range(300000, 302000))

    # 先用 AKShare 拿准确列表（更快更准）
    try:
        df = ak.stock_info_a_code_name()
        codes = df["code"].astype(str).str.zfill(6).tolist()
        codes = [c for c in codes if not c.startswith("8")]  # 排除北交所
        if len(codes) > 100:
            return codes
    except Exception:
        pass

    # 备用：直接枚举（可能有冗余，靠行情验证过滤）
    codes = [str(c).zfill(6) for c in sh_ranges + sz_ranges]
    return codes


def fetch_all_quotes(codes: list[str]) -> pd.DataFrame:
    """
    分批调用腾讯批量行情，返回全市场行情DataFrame。
    100只/批，每批~0.7s，1800只约13批~10s。
    """
    print(f"  分批拉取腾讯行情（{len(codes)}只，{CFG['batch_size']}只/批）...")
    t0 = time.time()
    all_rows = []
    symbols  = [to_qq_symbol(c) for c in codes]

    for i in range(0, len(symbols), CFG["batch_size"]):
        batch = symbols[i : i + CFG["batch_size"]]
        rows  = get_qq_batch(batch)
        all_rows.extend(rows)
        if (i // CFG["batch_size"] + 1) % 5 == 0:
            done = min(i + CFG["batch_size"], len(symbols))
            print(f"    进度 {done}/{len(symbols)}  有效 {len(all_rows)}", end="\r")

    df = pd.DataFrame(all_rows)
    print(f"  ✓ 腾讯行情: {len(df)} 只，耗时 {time.time()-t0:.1f}s      ")
    return df


def pre_filter(quote_df: pd.DataFrame) -> tuple[list, pd.DataFrame]:
    """根据实时行情做预筛，返回（候选代码列表，行情DF）"""
    print("\n【步骤2】预筛...")
    n0 = len(quote_df)
    df = quote_df.copy()
    masks = pd.Series(True, index=df.index)

    if CFG["exclude_st"] and "name" in df.columns:
        m = ~df["name"].str.contains("ST|退", na=False)
        print(f"  ST过滤:    -{(~m & masks).sum():4d} 只"); masks &= m

    if "price" in df.columns:
        m = df["price"].between(CFG["pre_min_price"], CFG["pre_max_price"])
        print(f"  股价过滤:  -{(~m & masks).sum():4d} 只"); masks &= m

    if "pct_chg" in df.columns:
        m = df["pct_chg"].between(CFG["pre_min_pct"], CFG["pre_max_pct"])
        print(f"  涨跌幅过滤:-{(~m & masks).sum():4d} 只"); masks &= m

    if "turnover" in df.columns:
        m = df["turnover"].between(CFG["pre_min_turnover"], CFG["pre_max_turnover"])
        print(f"  换手率过滤:-{(~m & masks).sum():4d} 只"); masks &= m

    if "volume" in df.columns:
        m = df["volume"] >= CFG["pre_min_amount"]
        print(f"  成交量过滤:-{(~m & masks).sum():4d} 只"); masks &= m

    df_ok = df[masks].copy()
    codes = df_ok["code"].tolist()
    print(f"  ✓ 预筛: {n0} → {len(codes)} 只")
    return codes, df_ok


# ============================================================
# ==================== 步骤3：日线并发 =======================
# ============================================================

def fetch_one_kline(code: str) -> tuple[str, pd.DataFrame]:
    """
    直接用腾讯HTTP接口拉日线，绕开AKShare封装（内部tqdm阻塞线程池）。
    接口: http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{n},qfq
    实测 ~0.5s/只，12线程可真正并发到 10+只/s。
    """
    prefix = "sh" if code[0] in ("6","5") else "sz"
    symbol = f"{prefix}{code}"
    n = CFG["n_days"]

    try:
        url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{n},qfq"
        r = _session.get(url, timeout=10, headers=_HDR)
        r.raise_for_status()
        j = r.json()

        # 数据结构: data -> {symbol} -> {day: [[date,open,close,high,low,volume],...]}
        # 或 data -> {symbol} -> qfqday: [...]
        outer = j.get("data", {})
        inner = outer.get(symbol, outer.get("qt" + symbol, {}))

        # 尝试多种可能的key
        kline_key = None
        for k in ("day", "qfqday", "days", "qfqdays"):
            if k in inner:
                kline_key = k
                break

        if kline_key is None:
            return code, pd.DataFrame()

        raw = inner[kline_key]
        if not raw:
            return code, pd.DataFrame()

        cols = ["date", "open", "close", "high", "low", "volume"]
        df = pd.DataFrame(raw, columns=cols)
        df["date"] = pd.to_datetime(df["date"])
        for c in ["open", "close", "high", "low", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df.dropna(subset=["close"], inplace=True)
        df.sort_values("date", inplace=True)

        if len(df) < CFG["min_valid_days"]:
            return code, pd.DataFrame()

        return code, df.tail(CFG["n_days"]).reset_index(drop=True)

    except Exception:
        return code, pd.DataFrame()


def fetch_all_daily(codes: list) -> dict:
    print(f"\n【步骤3】并发拉取日线（{CFG['max_workers']}线程 × {len(codes)}只，腾讯直连）...")
    est = len(codes) * 0.5 / CFG["max_workers"]
    print(f"  预计耗时: {est:.0f}s ~ {est*1.5:.0f}s")
    t0   = time.time()
    data = {}
    done = [0]
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=CFG["max_workers"]) as ex:
        futures = {ex.submit(fetch_one_kline, c): c for c in codes}
        for fut in as_completed(futures):
            code, df = fut.result()
            with lock:
                done[0] += 1
                if not df.empty:
                    data[code] = df
                n = len(codes)
                if done[0] % 50 == 0 or done[0] == n:
                    el = time.time() - t0
                    sp = done[0] / el if el > 0 else 1
                    rm = (n - done[0]) / sp
                    pct = done[0] / n * 100
                    bar = "█" * int(pct/5) + "░" * (20-int(pct/5))
                    print(f"  [{bar}] {pct:5.1f}%  {done[0]:4d}/{n}"
                          f"  有效{len(data):4d}  {sp:.0f}只/s  剩{rm:.0f}s")

    print(f"  ✓ 日线完成: {len(data)}/{len(codes)} 只，耗时 {time.time()-t0:.1f}s")
    return data


# ============================================================
# ==================== 指标计算 ==============================
# ============================================================

def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    out = np.empty(len(arr), dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i-1]
    return out


def calc_factors(close, high, low, volume, code) -> dict | None:
    """volume实为成交额（腾讯amount），用于量比计算，逻辑完全一致"""
    n = len(close)
    if n < CFG["min_valid_days"]:
        return None
    last_close = float(close[-1])
    if not (CFG["pre_min_price"] <= last_close <= CFG["pre_max_price"]):
        return None

    r = {"code": code, "close": round(last_close, 2)}

    # 1. 截面动量
    skip, win = CFG["momentum_skip"], CFG["momentum_window"]
    mom = float((close[-(skip+1)] - close[-(win+skip+1)]) /
                (close[-(win+skip+1)] + 1e-9)) if n >= win+skip+1 else 0.0
    r["momentum"] = round(mom, 4)
    r["momentum_score"] = round(
        min(max(mom / (CFG["momentum_threshold"]*3), 0), 1.0) if mom > 0 else 0.0, 3)

    # 2. 量价背离（用amount代替volume，计算量比）
    vw = CFG["vol_ratio_window"]
    vol_mean = volume[-(vw+1):-1].mean() if n >= vw+1 else volume.mean()
    vr = float(volume[-1]) / (float(vol_mean) + 1e-9)
    r["volume_ratio"] = round(vr, 2)
    cw = CFG["pv_corr_window"]
    if n >= cw:
        cp = close[-cw:] - close[-cw:].mean()
        cv = volume[-cw:] - volume[-cw:].mean()
        pv_corr = float((cp*cv).sum() /
                        (np.sqrt((cp**2).sum()*(cv**2).sum()) + 1e-9))
    else:
        pv_corr = 0.0
    r["pv_corr"] = round(pv_corr, 3)
    vr_min, vr_max = CFG["vol_ratio_min"], CFG["vol_ratio_max"]
    r["volume_score"] = round(
        (min((vr-vr_min)/(vr_max-vr_min), 1.0)*0.7 + min(pv_corr,1.0)*0.3)
        if vr_min <= vr <= vr_max and pv_corr > 0 else 0.0, 3)

    # 3. RSI
    d  = np.diff(close)
    ag = _ema(np.where(d > 0, d, 0.0), CFG["rsi_period"])
    al = _ema(np.where(d < 0, -d, 0.0), CFG["rsi_period"])
    rsi = float(100 - 100 / (1 + ag[-1] / (al[-1] + 1e-9)))
    r["rsi"] = round(rsi, 1)
    r["rsi_score"] = (0.0 if rsi >= CFG["rsi_overbought"] else
                      1.0 if rsi <= CFG["rsi_oversold"] else
                      round((CFG["rsi_overbought"]-rsi) /
                            (CFG["rsi_overbought"]-CFG["rsi_oversold"]), 3))

    # 4. MACD
    dif = _ema(close, CFG["macd_fast"]) - _ema(close, CFG["macd_slow"])
    dea = _ema(dif, CFG["macd_signal"])
    hist = (dif - dea) * 2
    ld, la = float(dif[-1]), float(dea[-1])
    lh, ph = float(hist[-1]), float(hist[-2]) if n > 1 else 0.0
    r["macd_dif"] = round(ld, 4); r["macd_dea"] = round(la, 4)
    if ld > la and ph <= 0 and lh > 0:    r["macd_score"]=1.0; r["macd_signal"]="金叉"
    elif ld > la and lh > 0 and lh > ph:  r["macd_score"]=0.7; r["macd_signal"]="多头"
    elif ld > la:                          r["macd_score"]=0.4; r["macd_signal"]="DIF>DEA"
    else:                                  r["macd_score"]=0.1; r["macd_signal"]="空头"

    # 5. 均线
    e5  = float(_ema(close, CFG["ema_short"])[-1])
    e10 = float(_ema(close, CFG["ema_mid"])[-1])
    e20 = float(_ema(close, CFG["ema_long"])[-1])
    r["ema5"]=round(e5,2); r["ema10"]=round(e10,2); r["ema20"]=round(e20,2)
    if last_close > e5 > e10 > e20:  r["ema_score"]=1.0; r["ema_signal"]="完美多头"
    elif last_close > e10 > e20:     r["ema_score"]=0.7; r["ema_signal"]="中期多头"
    elif last_close > e20:           r["ema_score"]=0.4; r["ema_signal"]="站上均线"
    elif e5 > e10:                   r["ema_score"]=0.3; r["ema_signal"]="短期金叉"
    else:                            r["ema_score"]=0.0; r["ema_signal"]="空头排列"

    # 6. DMI/ADX
    p  = CFG["adx_period"]
    tr = np.maximum(high[1:]-low[1:], np.maximum(
         np.abs(high[1:]-close[:-1]), np.abs(low[1:]-close[:-1])))
    up = high[1:]-high[:-1]; dn = low[:-1]-low[1:]
    atr = _ema(tr, p)
    pdi = 100 * _ema(np.where((up>dn)&(up>0), up, 0.0), p) / (atr+1e-9)
    mdi = 100 * _ema(np.where((dn>up)&(dn>0), dn, 0.0), p) / (atr+1e-9)
    adx_v = float(_ema(100*np.abs(pdi-mdi)/(pdi+mdi+1e-9), p)[-1])
    lpdi, lmdi = float(pdi[-1]), float(mdi[-1])
    r["plus_di"]=round(lpdi,1); r["minus_di"]=round(lmdi,1); r["adx"]=round(adx_v,1)
    if adx_v >= CFG["adx_threshold"] and lpdi > lmdi:
        r["adx_score"]=round(min(adx_v/50,1.0),3); r["adx_signal"]=f"强趋势(ADX={adx_v:.0f})"
    elif lpdi > lmdi:
        r["adx_score"]=0.3; r["adx_signal"]=f"弱趋势(ADX={adx_v:.0f})"
    else:
        r["adx_score"]=0.0; r["adx_signal"]=f"偏空(ADX={adx_v:.0f})"

    # 7. SAR
    af_i, af_m = CFG["sar_af_init"], CFG["sar_af_max"]
    sar = np.zeros(n); trend=1; ep=high[0]; af=af_i; sar[0]=low[0]
    for i in range(1, n):
        ps = sar[i-1]
        if trend == 1:
            sar[i] = min(ps+af*(ep-ps), low[i-1], low[max(0,i-2)])
            if low[i] < sar[i]:  trend=-1; sar[i]=ep; ep=low[i]; af=af_i
            elif high[i] > ep:   ep=high[i]; af=min(af+af_i, af_m)
        else:
            sar[i] = max(ps+af*(ep-ps), high[i-1], high[max(0,i-2)])
            if high[i] > sar[i]: trend=1; sar[i]=ep; ep=high[i]; af=af_i
            elif low[i] < ep:    ep=low[i]; af=min(af+af_i, af_m)
    ls = float(sar[-1]); r["sar"] = round(ls, 2)
    if ls < last_close:
        sd = (last_close-ls)/last_close
        r["sar_score"]=round(min(1.0, max(0.3, 1.0-sd*10)), 3)
        r["sar_signal"]=f"SAR支撑({sd*100:.1f}%)"
    else:
        r["sar_score"]=0.0; r["sar_signal"]="SAR压制"

    r["total_score"] = round(
        r["momentum_score"]*CFG["weight_momentum"] +
        r["volume_score"]  *CFG["weight_volume"]   +
        r["rsi_score"]     *CFG["weight_rsi"]       +
        r["macd_score"]    *CFG["weight_macd"]      +
        r["ema_score"]     *CFG["weight_ema"]       +
        r["adx_score"]     *CFG["weight_adx"]       +
        r["sar_score"]     *CFG["weight_sar"], 4)
    return r


# ============================================================
# ======================== 主流程 ============================
# ============================================================

def run_scanner():
    t_start = time.time()
    print("=" * 65)
    print("   A股多因子短期推荐扫描器 v4.0  （腾讯数据源）")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    print("因子权重: 动量20% 量价20% RSI15% MACD15% EMA15% ADX10% SAR5%")

    # 步骤1：获取股票代码列表
    print("\n【步骤1】获取股票代码列表...")
    t1 = time.time()
    all_codes = get_all_codes()
    print(f"  ✓ 代码列表: {len(all_codes)} 只  ({time.time()-t1:.1f}s)")

    # 步骤1b：腾讯批量行情（预筛数据来源）
    print("\n【步骤1b】腾讯批量行情（用于预筛）...")
    quote_df = fetch_all_quotes(all_codes)
    if quote_df.empty:
        print("❌ 行情获取失败，退出"); return

    # 步骤2：预筛
    candidates, rt_df = pre_filter(quote_df)
    if not candidates:
        print("❌ 预筛无候选，退出"); return

    # 步骤3：并发日线
    daily_data = fetch_all_daily(candidates)
    if not daily_data:
        print("❌ 日线数据为空，退出"); return

    # 步骤4：因子计算
    print(f"\n【步骤4】计算因子（{len(daily_data)}只）...")
    t4 = time.time()
    results = []
    for code, df in daily_data.items():
        try:
            r = calc_factors(
                df["close"].values.astype(float),
                df["high"].values.astype(float),
                df["low"].values.astype(float),
                df["volume"].values.astype(float),
                code)
            if r:
                results.append(r)
        except Exception:
            pass
    print(f"  ✓ 因子完成: {len(results)}只  ({time.time()-t4:.1f}s)")
    if not results:
        print("❌ 无有效因子结果"); return

    # 步骤5：多级筛选
    print("\n【步骤5】多级筛选...")
    df_all = pd.DataFrame(results)
    print(f"  初始候选: {len(df_all)} 只")

    df_f = df_all.copy()
    filter_steps = [
        ("动量为负", lambda d: d["momentum"] >= 0),
        ("RSI超买",  lambda d: d["rsi"] < CFG["rsi_overbought"]),
        ("MACD空头", lambda d: d["macd_dif"] > d["macd_dea"]),
        ("SAR压制",  lambda d: d["sar"] < d["close"]),
        ("无趋势",   lambda d: (d["adx"] >= CFG["adx_threshold"]) | (d["ema_score"] >= 0.7)),
    ]
    for label, fn in filter_steps:
        nb = len(df_f)
        df_f = df_f[fn(df_f)]
        print(f"  过滤{label}: -{nb-len(df_f):4d} 只 → 剩余 {len(df_f)} 只")
        if df_f.empty:
            break

    nb    = len(df_f)
    vr_ok = df_f[df_f["volume_ratio"] >= CFG["vol_ratio_min"]]
    df_f  = vr_ok if not vr_ok.empty else df_f
    print(f"  过滤缩量:   -{nb-len(df_f):4d} 只 → 剩余 {len(df_f)} 只"
          + ("" if not vr_ok.empty else "（已放宽）"))

    if df_f.empty:
        print("  ⚠ 条件过严，回退至动量+MACD...")
        df_f = df_all[(df_all["momentum"] >= 0) & (df_all["macd_dif"] > df_all["macd_dea"])]

    df_final = df_f.sort_values("total_score", ascending=False).head(CFG["top_n"]).copy()

    # 补充名称 + 今日涨跌幅
    name_dict = dict(zip(rt_df["code"], rt_df["name"])) if "name" in rt_df.columns else {}
    df_final["name"] = df_final["code"].map(name_dict).fillna("--")
    if "pct_chg" in rt_df.columns:
        df_final = df_final.merge(rt_df[["code","pct_chg"]], on="code", how="left")

    # ── 输出 ─────────────────────────────────────────────────
    print("\n" + "="*65)
    print(f"  【最终推荐 TOP {CFG['top_n']}】  {datetime.now().strftime('%Y-%m-%d')}")
    print("="*65)
    show = ["code","name","close","total_score","momentum",
            "rsi","macd_signal","ema_signal","adx_signal","volume_ratio"]
    show = [c for c in show if c in df_final.columns]
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    print(df_final[show].to_string(index=False))

    print("\n" + "="*65)
    print("  【逐股详细分析】")
    print("="*65)
    for rank, (_, row) in enumerate(df_final.iterrows(), 1):
        print(f"\n{'─'*57}")
        print(f"  #{rank:02d}  {row['code']} {row.get('name','--')}"
              f"   ¥{row['close']}   综合得分: {row['total_score']:.3f}")
        print(f"{'─'*57}")
        print(f"  [动量]  近{CFG['momentum_window']}日涨幅: {row['momentum']*100:+.1f}%"
              f"   得分: {row['momentum_score']:.2f}")
        print(f"  [量价]  量比: {row['volume_ratio']:.1f}x"
              f"  量价相关: {row['pv_corr']:+.2f}   得分: {row['volume_score']:.2f}")
        tag = ("⚠超卖" if row["rsi"] <= CFG["rsi_oversold"] else
               "⚠超买" if row["rsi"] >= CFG["rsi_overbought"] else "正常")
        print(f"  [RSI]   RSI({CFG['rsi_period']}): {row['rsi']:.1f} {tag}"
              f"   得分: {row['rsi_score']:.2f}")
        print(f"  [MACD]  {row['macd_signal']}"
              f"  DIF={row['macd_dif']:.4f} DEA={row['macd_dea']:.4f}"
              f"   得分: {row['macd_score']:.2f}")
        print(f"  [EMA]   {row['ema_signal']}"
              f"  EMA5={row['ema5']} EMA10={row['ema10']} EMA20={row['ema20']}"
              f"   得分: {row['ema_score']:.2f}")
        print(f"  [ADX]   {row['adx_signal']}"
              f"  +DI={row['plus_di']:.1f} -DI={row['minus_di']:.1f}"
              f"   得分: {row['adx_score']:.2f}")
        print(f"  [SAR]   {row['sar_signal']}  SAR={row['sar']}"
              f"   得分: {row['sar_score']:.2f}")
        if "pct_chg" in row.index and pd.notna(row.get("pct_chg")):
            print(f"  [今日]  涨跌: {row['pct_chg']:+.2f}%")
        score = row["total_score"]
        print(f"  [建议]  "
              f"{'★★★ 强烈关注，多因子共振' if score>=0.65 else '★★  关注，多数指标偏多' if score>=0.50 else '★   参考，注意风控'}")

    if CFG["output_csv"]:
        csv_path = f"result_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        df_final.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"\n✓ 已保存: {csv_path}")

    print(f"\n⏱ 总耗时: {time.time()-t_start:.0f}s")
    print("\n" + "="*65)
    print("  ⚠ 仅供研究，不构成投资建议，T+1注意跳空风险")
    print("="*65)
    return df_final


if __name__ == "__main__":
    try:
        run_scanner()
    except KeyboardInterrupt:
        print("\n用户中断"); sys.exit(0)
    except Exception as e:
        import traceback
        print(f"\n❌ {e}"); traceback.print_exc(); sys.exit(1)