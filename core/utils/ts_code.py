"""Stock code format conversions."""


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
