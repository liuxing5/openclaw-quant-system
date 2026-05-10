"""
中国 A 股交易日历
================
包含 A 股休市日期（周末 + 法定节假日）

数据来源：上海证券交易所、深圳证券交易所公告
更新规则：每年末更新下一年休市日期；未硬编码的年份自动从 akshare 懒加载并缓存

休市类型：
  - 周末：周六、周日（自动判断，无需配置）
  - 法定节假日：元旦、春节、清明、劳动节、端午、中秋、国庆等
  - 调休工作日：周末调休上班但股市仍休市
"""

import json
import os
from datetime import date, timedelta
from typing import Set

# ============================================================
# A 股休市日期配置（仅配置非周末的休市日）
# 格式：YYYY-MM-DD
# ============================================================

HOLIDAYS_2025: Set[str] = {
    # 元旦
    "2025-01-01",  # 周三 元旦
    # 春节
    "2025-01-28",  # 周二 除夕
    "2025-01-29",  # 周三 春节
    "2025-01-30",  # 周四 初二
    "2025-01-31",  # 周五 初三
    # 清明
    "2025-04-04",  # 周五 清明
    # 劳动节（5天假期：5月1日-5月5日）
    "2025-05-01",  # 周四 劳动节
    "2025-05-02",  # 周五 劳动节假期
    "2025-05-05",  # 周一 劳动节假期（调休）
    # 端午
    "2025-05-30",  # 周五 端午
    # 中秋 + 国庆
    "2025-10-01",  # 周三 国庆
    "2025-10-02",  # 周四 国庆
    "2025-10-03",  # 周五 国庆
    "2025-10-06",  # 周一 国庆调休
    "2025-10-07",  # 周二 国庆调休
    "2025-10-08",  # 周三 国庆调休
}

HOLIDAYS_2026: Set[str] = {
    # 元旦
    "2026-01-01",  # 周四 元旦
    "2026-01-02",  # 周五 元旦假期
    # 春节（7天假期）
    "2026-02-16",  # 周一 除夕
    "2026-02-17",  # 周二 春节
    "2026-02-18",  # 周三 初二
    "2026-02-19",  # 周四 初三
    "2026-02-20",  # 周五 初四
    "2026-02-23",  # 周一 春节调休
    # 清明
    "2026-04-06",  # 周一 清明调休
    # 劳动节（5天假期：5月1日-5月5日）
    "2026-05-01",  # 周五 劳动节
    "2026-05-04",  # 周一 劳动节假期（调休）
    "2026-05-05",  # 周二 劳动节假期（调休）
    # 端午
    "2026-06-19",  # 周五 端午
    # 中秋
    "2026-09-25",  # 周五 中秋
    # 国庆（7天假期）
    "2026-10-01",  # 周四 国庆
    "2026-10-02",  # 周五 国庆
    "2026-10-05",  # 周一 国庆调休
    "2026-10-06",  # 周二 国庆调休
    "2026-10-07",  # 周三 国庆调休
    "2026-10-08",  # 周四 国庆调休
    "2026-10-09",  # 周五 国庆调休
}

# 合并所有硬编码休市日期（year -> set of dates）
HARDCODED_HOLIDAYS: dict = {
    2025: HOLIDAYS_2025,
    2026: HOLIDAYS_2026,
}
ALL_HOLIDAYS: Set[str] = HOLIDAYS_2025 | HOLIDAYS_2026

# 未硬编码年份的 akshare 兜底缓存
# 仓库内不存这个文件，让 actions/cache 持久化
_DYNAMIC_CACHE_FILE = os.environ.get(
    "ZUIYOU_TRADING_CALENDAR_CACHE",
    os.path.expanduser("~/.cache/zuiyou_trading_calendar.json"),
)
_dynamic_holidays: dict = {}  # year -> set of "YYYY-MM-DD"


def _load_dynamic_cache() -> None:
    global _dynamic_holidays
    if _dynamic_holidays:
        return
    if os.path.exists(_DYNAMIC_CACHE_FILE):
        try:
            with open(_DYNAMIC_CACHE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            _dynamic_holidays = {int(y): set(dates) for y, dates in raw.items()}
        except Exception:
            _dynamic_holidays = {}


def _save_dynamic_cache() -> None:
    try:
        os.makedirs(os.path.dirname(_DYNAMIC_CACHE_FILE), exist_ok=True)
        with open(_DYNAMIC_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {str(y): sorted(dates) for y, dates in _dynamic_holidays.items()},
                f, ensure_ascii=False,
            )
    except Exception:
        pass


def _fetch_holidays_from_akshare(year: int) -> Set[str]:
    """从 akshare 获取指定年份所有交易日，反推得到节假日集合"""
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        # df['trade_date'] 是 datetime.date 对象
        trading = {
            d.strftime("%Y-%m-%d") for d in df['trade_date']
            if hasattr(d, 'year') and d.year == year
        }
        if not trading:
            return set()
        # 反推节假日：周一到周五但不在交易日列表里 → 节假日
        holidays = set()
        d = date(year, 1, 1)
        end = date(year, 12, 31)
        while d <= end:
            if d.weekday() < 5 and d.strftime("%Y-%m-%d") not in trading:
                holidays.add(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        return holidays
    except Exception as e:
        print(f"⚠️ akshare 节假日查询失败 (year={year}): {e}")
        return set()


def _ensure_year_loaded(year: int) -> Set[str]:
    """返回指定年份的节假日集合：硬编码 > 动态缓存 > akshare 拉取并缓存"""
    if year in HARDCODED_HOLIDAYS:
        return HARDCODED_HOLIDAYS[year]
    _load_dynamic_cache()
    if year in _dynamic_holidays:
        return _dynamic_holidays[year]
    fetched = _fetch_holidays_from_akshare(year)
    if fetched:
        _dynamic_holidays[year] = fetched
        _save_dynamic_cache()
        return fetched
    # akshare 也失败 → 返回空集合，仅周末判定（保守做法：宁可误判为交易日不要错过推送）
    return set()


def is_trading_day(d: date = None) -> bool:
    """
    判断指定日期是否为 A 股交易日。

    Args:
        d: 日期，默认为今天

    Returns:
        True = 交易日, False = 休市日

    未硬编码的年份会懒加载 akshare 并缓存到 ~/.cache/zuiyou_trading_calendar.json。
    """
    if d is None:
        d = date.today()

    # 周末休市
    if d.weekday() >= 5:  # 5=周六, 6=周日
        return False

    # 法定节假日休市
    date_str = d.strftime("%Y-%m-%d")
    holidays = _ensure_year_loaded(d.year)
    if date_str in holidays:
        return False

    return True


def get_next_trading_day(d: date = None, days_ahead: int = 1) -> date:
    """
    获取从今天起第 N 个交易日。

    Args:
        d: 起始日期，默认为今天
        days_ahead: 往前数第几个交易日（默认 1 = 下一个交易日）

    Returns:
        目标交易日的 date 对象
    """
    if d is None:
        d = date.today()

    count = 0
    current = d + timedelta(days=1)
    while count < days_ahead:
        if is_trading_day(current):
            count += 1
            if count == days_ahead:
                return current
        current += timedelta(days=1)
    return current


def get_trading_days_in_range(start: date, end: date) -> list:
    """
    获取日期范围内的所有交易日。

    Args:
        start: 起始日期
        end: 结束日期

    Returns:
        交易日列表 [date, ...]
    """
    days = []
    current = start
    while current <= end:
        if is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


if __name__ == "__main__":
    from datetime import date

    today = date.today()
    weekdays = ['一', '二', '三', '四', '五', '六', '日']
    print(f"📅 今天: {today} ({weekdays[today.weekday()]})")
    print(f"📊 是否交易日: {'是' if is_trading_day(today) else '否（休市）'}")

    next_day = get_next_trading_day(today)
    print(f" 下一个交易日: {next_day}")

    # 测试五一期间
    print("\n🧪 测试 2025 年五一期间:")
    for day in range(1, 7):
        d = date(2025, 5, day)
        print(f"  {d} ({weekdays[d.weekday()]}) : {'交易日' if is_trading_day(d) else '休市'}")

    # 测试 2026 年五一期间
    print("\n 测试 2026 年五一期间:")
    for day in range(1, 7):
        d = date(2026, 5, day)
        print(f"  {d} ({weekdays[d.weekday()]}) : {'交易日' if is_trading_day(d) else '休市'}")
