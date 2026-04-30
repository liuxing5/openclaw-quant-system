"""
中国 A 股交易日历
================
包含 A 股休市日期（周末 + 法定节假日）

数据来源：上海证券交易所、深圳证券交易所公告
更新规则：每年末更新下一年休市日期

休市类型：
  - 周末：周六、周日（自动判断，无需配置）
  - 法定节假日：元旦、春节、清明、劳动节、端午、中秋、国庆等
  - 调休工作日：周末调休上班但股市仍休市
"""

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

# 合并所有休市日期
ALL_HOLIDAYS: Set[str] = HOLIDAYS_2025 | HOLIDAYS_2026


def is_trading_day(d: date = None) -> bool:
    """
    判断指定日期是否为 A 股交易日。

    Args:
        d: 日期，默认为今天

    Returns:
        True = 交易日, False = 休市日
    """
    if d is None:
        d = date.today()

    # 周末休市
    if d.weekday() >= 5:  # 5=周六, 6=周日
        return False

    # 法定节假日休市
    date_str = d.strftime("%Y-%m-%d")
    if date_str in ALL_HOLIDAYS:
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
