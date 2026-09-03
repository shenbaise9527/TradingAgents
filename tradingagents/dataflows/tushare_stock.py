from datetime import datetime
from typing import Annotated

from .errors import NoMarketDataError
from .stockstats_utils import _assert_ohlcv_not_stale
from .tushare_common import (
    daily_to_ohlcv_frame,
    fetch_daily_bars,
    normalize_ts_code,
)


def get_stock(
    symbol: Annotated[str, "ticker symbol of the company (e.g. 600000.SS, 000001.SZ)"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Fetch daily OHLCV data from Tushare for China A-shares.

    Returns a ``# header + CSV`` string shaped exactly like the yfinance
    vendor's ``get_stock_data`` output, so the LLM sees one format regardless
    of the configured vendor. Prices are 前复权 (qfq) so they line up with the
    yfinance ``auto_adjust`` figures used by the verified-market snapshot.

    Raises:
        NoMarketDataError: non-A-share symbol, empty range, stale frame, or an
            unexpected response shape — the router emits one NO_DATA sentinel
            (and falls back to a next vendor when configured).
    """
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    # Typed rejection of non-CN symbols before any API spend.
    ts_code = normalize_ts_code(symbol)
    raw = fetch_daily_bars(ts_code, start_date, end_date, adj="qfq")

    if raw is None or raw.empty:
        raise NoMarketDataError(
            symbol, ts_code, f"no rows between {start_date} and {end_date}"
        )
    result = daily_to_ohlcv_frame(raw, symbol=symbol, ts_code=ts_code)

    # Reject a frame whose latest bar is far older than the requested end date
    # (delisted, long-suspended, or a bad partial response) — same guard the
    # yfinance path applies (#1021 semantics).
    _assert_ohlcv_not_stale(result, end_date, symbol, ts_code)

    requested = symbol.strip().upper()
    label = ts_code if ts_code == requested else f"{ts_code} (from {symbol})"
    header = f"# Stock data for {label} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(result)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    return header + result.to_csv(index=False)
