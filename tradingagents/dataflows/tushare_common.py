import os
import logging

logger = logging.getLogger(__name__)

_pro_api = None


class TushareRateLimitError(Exception):
    """Exception raised when Tushare API rate/credit limit is exceeded."""
    pass


def get_api_token() -> str:
    """Retrieve the Tushare API token from environment variables."""
    token = os.getenv("TUSHARE_API_TOKEN")
    if not token:
        raise ValueError(
            "TUSHARE_API_TOKEN environment variable is not set. "
            "Get your token at https://tushare.pro/register"
        )
    return token


def get_pro_api():
    """Get a cached Tushare pro_api instance (lazy singleton)."""
    global _pro_api
    if _pro_api is None:
        import tushare as ts
        _pro_api = ts.pro_api(get_api_token())
    return _pro_api


def to_tushare_date(date_str: str) -> str:
    """Convert 'yyyy-mm-dd' to 'YYYYMMDD' format used by Tushare."""
    return date_str.replace("-", "")


def from_tushare_date(date_str: str) -> str:
    """Convert 'YYYYMMDD' to 'yyyy-mm-dd' format used by the framework."""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"


def normalize_ts_code(symbol: str) -> str:
    """Normalize a ticker symbol to Tushare ts_code format (e.g., '000001.SZ').

    Rules:
        - Already has .SH/.SZ/.BJ suffix -> return uppercased as-is
        - 6-digit code starting with 6 -> append .SH (Shanghai)
        - 6-digit code starting with 0 or 3 -> append .SZ (Shenzhen)
        - 6-digit code starting with 8 or 4 -> append .BJ (Beijing/BSE)
        - Otherwise -> return as-is with warning
    """
    symbol = symbol.strip().upper()

    # Already has exchange suffix
    if symbol.endswith((".SH", ".SZ", ".BJ")):
        return symbol

    # Strip any other suffix (e.g., .SS used by Yahoo for Shanghai)
    bare = symbol.split(".")[0] if "." in symbol else symbol

    if len(bare) == 6 and bare.isdigit():
        first = bare[0]
        if first == "6":
            return f"{bare}.SH"
        elif first in ("0", "3"):
            return f"{bare}.SZ"
        elif first in ("8", "4"):
            return f"{bare}.BJ"

    logger.warning(
        f"Cannot determine exchange for symbol '{symbol}', returning as-is"
    )
    return symbol


def tushare_api_call(api_method, **kwargs):
    """Call a Tushare pro_api method with rate-limit error handling.

    Args:
        api_method: A bound method on the pro_api instance (e.g., pro.daily).
        **kwargs: Arguments forwarded to the API method.

    Returns:
        pandas.DataFrame returned by the API.

    Raises:
        TushareRateLimitError: When the API reports a rate or credit limit.
    """
    try:
        result = api_method(**kwargs)
        return result
    except Exception as e:
        msg = str(e).lower()
        if any(kw in msg for kw in ("限制", "limit", "频率", "credits", "积分", "权限")):
            raise TushareRateLimitError(f"Tushare rate/credit limit: {e}") from e
        raise
