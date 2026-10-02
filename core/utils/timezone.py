from datetime import date, datetime, timedelta, timezone

_BEIJING = timezone(timedelta(hours=8))


def now_beijing() -> datetime:
    """Return current moment at fixed UTC+8 (China has no DST)."""
    return datetime.now(_BEIJING)


def beijing_today() -> date:
    """Return current trade date at UTC+8, not server-local date."""
    return now_beijing().date()
