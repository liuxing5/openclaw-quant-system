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
    pure = code.lstrip('0') or code
    if pure.startswith(('6', '688')):
        return f"{code}.SH"
    elif pure.startswith(('8', '4')):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


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
