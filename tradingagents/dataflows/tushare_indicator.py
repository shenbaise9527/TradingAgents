import os
from datetime import datetime
from typing import Annotated

import pandas as pd
from dateutil.relativedelta import relativedelta
from stockstats import wrap

from .config import get_config
from .errors import NoMarketDataError
from .stockstats_utils import (
    INDICATOR_DESCRIPTIONS,
    _assert_ohlcv_not_stale,
    _clean_dataframe,
    _fill_price_gaps,
    _needs_same_day_refresh,
)
from .tushare_common import (
    daily_to_ohlcv_frame,
    fetch_daily_bars,
    normalize_ts_code,
    shanghai_now,
)
from .utils import safe_ticker_component

# History window mirrors the yfinance ``load_ohlcv`` path: 5 years ending
# today (Shanghai time) so long-window indicators (200 SMA) have warm-up data.
_HISTORY_YEARS = 5


def _fetch_history(
    symbol: str, ts_code: str, curr_date: str
) -> pd.DataFrame:
    """Fetch 5y of qfq daily bars with disk cache, trimmed to the analysis date.

    Mirrors the guarantee pipeline of ``stockstats_utils.load_ohlcv`` — the
    same cache directory, the same-day refresh TTL, look-ahead trimming to
    ``curr_date``, and the stale-frame rejection — so indicator values have
    point-in-time semantics identical to the yfinance path. A cached file is
    never served empty; empty/column-less caches are refetched.
    """
    config = get_config()
    cache_dir = config["data_cache_dir"]
    os.makedirs(cache_dir, exist_ok=True)

    today_dt = pd.Timestamp(shanghai_now().date())
    curr_dt = pd.to_datetime(curr_date, errors="coerce").normalize()
    if pd.isna(curr_dt):
        raise NoMarketDataError(symbol, ts_code, f"invalid analysis date {curr_date!r}")

    start_dt = today_dt - pd.DateOffset(years=_HISTORY_YEARS)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = today_dt.strftime("%Y-%m-%d")
    safe = safe_ticker_component(ts_code)
    data_file = os.path.join(
        cache_dir, f"{safe}-Tushare-daily-{start_str}-{end_str}.csv"
    )

    data = None
    if os.path.exists(data_file):
        cached = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
        # Serve the cache only when usable and not a stale snapshot of the day
        # being requested; otherwise refetch (same rules as load_ohlcv, #1150).
        if (
            not cached.empty
            and "Close" in cached.columns
            and not _needs_same_day_refresh(data_file, curr_dt, today_dt)
        ):
            data = cached

    if data is None:
        downloaded = fetch_daily_bars(ts_code, start_str, end_str, adj="qfq")
        frame = daily_to_ohlcv_frame(downloaded, symbol=symbol, ts_code=ts_code)
        if frame.empty:
            raise NoMarketDataError(
                symbol, ts_code, "tushare returned no daily rows for the history window"
            )
        frame.to_csv(data_file, index=False, encoding="utf-8")
        data = frame

    data = _clean_dataframe(data)
    # Point-in-time: drop any row after the analysis date before computing
    # indicators, so a backtest never sees the future (#1021 semantics).
    data = data[data["Date"] <= curr_dt]
    if data.empty:
        raise NoMarketDataError(
            symbol, ts_code, f"no rows on or before {curr_date}"
        )
    data = _fill_price_gaps(data)
    _assert_ohlcv_not_stale(data, curr_date, symbol, ts_code)
    return data


def get_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    """Compute technical indicators for A-shares using Tushare + stockstats.

    Output shape and wording mirror ``get_stock_stats_indicators_window`` (the
    yfinance path) and the indicator descriptions come from the same shared
    ``INDICATOR_DESCRIPTIONS`` source, so agent-facing behaviour does not vary
    with the configured vendor.
    """
    datetime.strptime(curr_date, "%Y-%m-%d")
    ts_code = normalize_ts_code(symbol)

    indicator = (indicator or "").strip().lower()
    if indicator not in INDICATOR_DESCRIPTIONS:
        raise ValueError(
            f"Indicator {indicator} is not supported. "
            f"Please choose from: {list(INDICATOR_DESCRIPTIONS.keys())}"
        )

    data = _fetch_history(symbol, ts_code, curr_date)
    df = wrap(data)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

    df[indicator]  # Trigger stockstats to calculate the indicator.

    indicator_map = {}
    for _, row in df.iterrows():
        value = row[indicator]
        indicator_map[row["Date"]] = "N/A" if pd.isna(value) else str(value)

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = end_dt - relativedelta(days=look_back_days)

    lines = []
    current_dt = end_dt
    while current_dt >= before:
        date_str = current_dt.strftime("%Y-%m-%d")
        lines.append(
            f"{date_str}: {indicator_map.get(date_str, 'N/A: Not a trading day (weekend or holiday)')}"
        )
        current_dt -= relativedelta(days=1)

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to "
        f"{end_dt.strftime('%Y-%m-%d')}:\n\n"
        + "\n".join(lines)
        + "\n\n"
        + INDICATOR_DESCRIPTIONS[indicator]
    )
