import logging
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from .errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

logger = logging.getLogger(__name__)

_pro_api = None

# China A-share exchanges run on Asia/Shanghai time. Prefer the system tzdata
# via zoneinfo; fall back to a fixed UTC+8 offset so module import can never
# fail on a host without tzdata (this module is imported by every dataflow
# entry point through interface.py).
try:
    from zoneinfo import ZoneInfo

    _CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001 — missing/unknown tzdata must not break import
    _CN_TZ = timezone(timedelta(hours=8))

# tushare error-message keyword tables, so the routing layer reacts by the
# *behaviour* of the failure rather than the raw text. tushare's messages are
# Chinese; English equivalents are kept for safety. Entitlement (permission /
# points) failures mean the account cannot access this interface at all — the
# router treats that as "vendor not configured for this method" and falls
# back. Genuine throttling maps to the rate-limit type (auto-retry semantics).
_TUSHARE_RATE_LIMIT_MARKERS = (
    "每分钟", "频率", "限流", "访问限制",
    "rate limit", "too frequent", "api rate",
)
_TUSHARE_ENTITLEMENT_MARKERS = (
    "权限", "积分", "没有访问该接口",
    "permission", "not entitled", "no access",
)


def shanghai_now() -> datetime:
    """Return the current time in the China A-share trading timezone (UTC+8)."""
    return datetime.now(_CN_TZ)


class TushareRateLimitError(VendorRateLimitError):
    """Raised when the Tushare API rate/credit limit is exceeded.

    Subclasses ``VendorRateLimitError`` so the routing layer in
    ``interface.route_to_vendor`` automatically falls back to the next vendor.
    """
    pass


class TushareNotConfiguredError(VendorNotConfiguredError):
    """Raised when TUSHARE_API_TOKEN is missing but a tushare call is made.

    Subclasses ``VendorNotConfiguredError`` (a ``ValueError``), so the router
    treats tushare as unavailable and tries the next configured vendor.
    """
    pass


def get_api_token() -> str:
    """Retrieve the Tushare API token from environment variables.

    ``TUSHARE_API_TOKEN`` is the primary variable; ``TUSHARE_TOKEN`` is accepted
    as an alias (common in shell setups). Raises ``TushareNotConfiguredError``
    (a ``ValueError``) when neither is set.
    """
    token = os.getenv("TUSHARE_API_TOKEN") or os.getenv("TUSHARE_TOKEN")
    if not token:
        raise TushareNotConfiguredError(
            "TUSHARE_API_TOKEN (or its TUSHARE_TOKEN alias) environment "
            "variable is not set. Get your token at https://tushare.pro/register"
        )
    return token


def _tushare_module():
    """Return the tushare package, raising a typed error when not installed."""
    try:
        import tushare as ts
        return ts
    except ImportError as exc:  # pragma: no cover — env-dependent
        raise TushareNotConfiguredError(
            "the 'tushare' package is not installed; install it with "
            "'pip install \".[tushare]\"'"
        ) from exc


def get_pro_api():
    """Get a cached Tushare pro_api instance (lazy singleton)."""
    global _pro_api
    if _pro_api is None:
        _pro_api = _tushare_module().pro_api(get_api_token())
    return _pro_api


def fetch_daily_bars(
    ts_code: str,
    start_date: str,
    end_date: str,
    adj: str = "qfq",
):
    """Fetch (optionally adjusted) daily OHLCV bars from tushare pro_bar.

    Args:
        ts_code: tushare code like ``600000.SH`` (already normalized).
        start_date: inclusive window start in yyyy-mm-dd.
        end_date: inclusive window end in yyyy-mm-dd.
        adj: adjustment — "qfq" (前复权, default) so prices line up with the
            yfinance ``auto_adjust`` path used by the verified-market snapshot.

    Returns:
        pandas.DataFrame with the raw tushare daily columns (``trade_date``,
        ``open``, ``high``, ``low``, ``close``, ``vol``, ``amount``, ...).

    Raises:
        TushareRateLimitError / TushareNotConfiguredError for the classified
        API failures; the router reacts and falls back to the next vendor.
    """
    ts = _tushare_module()
    pro = get_pro_api()
    return tushare_api_call(
        ts.pro_bar,
        api=pro,
        ts_code=ts_code,
        adj=adj,
        start_date=to_tushare_date(start_date),
        end_date=to_tushare_date(end_date),
    )


def daily_to_ohlcv_frame(raw, *, symbol: str | None = None, ts_code: str | None = None):
    """Map a raw tushare daily frame to the framework OHLCV shape.

    Returns a DataFrame with ``Date/Open/High/Low/Close/Volume`` columns
    (date strings yyyy-mm-dd, prices rounded to 2dp, Volume in shares —
    tushare reports 手 = 100 shares), sorted ascending by date. Raises
    ``NoMarketDataError`` when required columns are missing.
    """
    if raw is None or raw.empty:
        return pd.DataFrame()
    required = {"trade_date", "open", "high", "low", "close", "vol"}
    missing = required - set(raw.columns)
    if missing:
        raise NoMarketDataError(
            symbol or ts_code or "?",
            ts_code,
            f"unexpected tushare response, missing columns {sorted(missing)}",
        )
    df = raw.sort_values("trade_date").reset_index(drop=True)
    return pd.DataFrame(
        {
            "Date": df["trade_date"].map(from_tushare_date),
            "Open": df["open"].astype(float).round(2),
            "High": df["high"].astype(float).round(2),
            "Low": df["low"].astype(float).round(2),
            "Close": df["close"].astype(float).round(2),
            "Volume": (df["vol"].astype(float) * 100).astype("int64"),
        }
    )


def to_tushare_date(date_str: str) -> str:
    """Convert 'yyyy-mm-dd' to 'YYYYMMDD' format used by Tushare."""
    return date_str.replace("-", "")


def from_tushare_date(date_str: str) -> str:
    """Convert 'YYYYMMDD' to 'yyyy-mm-dd' format used by the framework."""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"


def normalize_ts_code(symbol: str) -> str:
    """Normalize a ticker symbol to Tushare ts_code format (e.g., '600000.SH').

    Accepts a bare 6-digit code or any CN-suffixed form — ``.SH`` (tushare),
    ``.SS`` (Yahoo Shanghai), ``.SZ`` (both), ``.BJ`` (Beijing) — and derives
    the exchange from the leading digit:
        - 6xxxxx -> .SH (Shanghai)
        - 0/3xxxxx -> .SZ (Shenzhen)
        - 8/4xxxxx -> .BJ (Beijing/BSE)

    Raises ``NoMarketDataError`` for symbols that are not China A-share
    equities (US/HK/crypto/index codes), so the vendor router falls back to
    the next vendor without spending an API call.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise NoMarketDataError(symbol, None, "empty symbol")
    symbol = symbol.strip().upper()

    # Strip any recognized CN suffix (.SH/.SS/.SZ/.BJ), then re-derive the
    # exchange from the leading digit so the code wins over a wrong suffix.
    bare = symbol
    for suffix in (".SH", ".SS", ".SZ", ".BJ"):
        if symbol.endswith(suffix):
            bare = symbol[: -len(suffix)]
            break

    if len(bare) == 6 and bare.isdigit():
        first = bare[0]
        if first == "6":
            return f"{bare}.SH"
        if first in ("0", "3"):
            return f"{bare}.SZ"
        if first in ("8", "4"):
            return f"{bare}.BJ"

    raise NoMarketDataError(
        symbol,
        symbol,
        "not a China A-share equity (expected a 6-digit code: 6xxxxx=SH, "
        "0/3xxxxx=SZ, 8/4xxxxx=BJ)",
    )


def _classify_tushare_error(message: str):
    """Return the typed error class for a tushare error message, or None."""
    lowered = message.lower()
    if any(kw in lowered for kw in _TUSHARE_ENTITLEMENT_MARKERS):
        return TushareNotConfiguredError
    if any(kw in lowered for kw in _TUSHARE_RATE_LIMIT_MARKERS):
        return TushareRateLimitError
    return None


def tushare_api_call(api_method, **kwargs):
    """Call a Tushare pro_api method with rate-limit error handling.

    Args:
        api_method: A bound method on the pro_api instance (e.g., pro.daily).
        **kwargs: Arguments forwarded to the API method.

    Returns:
        pandas.DataFrame returned by the API.

    Raises:
        TushareRateLimitError: When the API reports a rate or credit limit.
        TushareNotConfiguredError: When the account lacks permission/points for
            the interface (``权限``/``积分``) or the package is not installed.
    """
    try:
        result = api_method(**kwargs)
        return result
    except Exception as e:  # noqa: BLE001 — must classify vendor error text
        error_cls = _classify_tushare_error(str(e))
        if error_cls is TushareNotConfiguredError:
            raise TushareNotConfiguredError(f"Tushare access denied: {e}") from e
        if error_cls is TushareRateLimitError:
            raise TushareRateLimitError(f"Tushare rate/credit limit: {e}") from e
        raise
