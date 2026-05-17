"""Stock code format conversions."""


def pure_to_ts_code(code: str) -> str:
    """6-digit pure code -> tushare ts_code (含北交所).

    >>> pure_to_ts_code('600519')
    '600519.SH'
    >>> pure_to_ts_code('000001')
    '000001.SZ'
    >>> pure_to_ts_code('430047')
    '430047.BJ'
    >>> pure_to_ts_code('830946')
    '830946.BJ'
    """
    if not code:
        return ''
    digits = ''.join(c for c in code if c.isdigit())
    if len(digits) < 6:
        return f"{code}.SZ"
    code6 = digits[:6]
    if code6.startswith(('6', '9')):
        return f"{code6}.SH"
    elif code6.startswith(('8', '4')):
        return f"{code6}.BJ"
    else:
        return f"{code6}.SZ"


def baostock_to_standard(code: str) -> str:
    """'sz.000001' -> '000001.SZ', 'sh.600519' -> '600519.SH'."""
    if '.' not in code:
        return code
    market, num = code.split('.', 1)
    return f"{num}.{market.upper()}"


def standard_to_baostock(code: str) -> str:
    """'000001.SZ' -> 'sz.000001'."""
    if '.' not in code:
        return code
    num, market = code.split('.', 1)
    return f"{market.lower()}.{num}"
