# Multi-layer data fetchers


class FetchResult(list):
    """List subclass that supports attribute assignment for structured data.

    Fetcher functions return FetchResult (behaves like a list of signals)
    with attached structured data via attributes.
    collector.py reads these via hasattr/getattr to feed store_structured.py.
    Plain list objects reject attribute assignment (AttributeError).
    """
    pass
