import os
from typing import Annotated
from datetime import datetime
from dateutil.relativedelta import relativedelta

import pandas as pd

from .tushare_common import (
    get_pro_api,
    normalize_ts_code,
    to_tushare_date,
    from_tushare_date,
    tushare_api_call,
)
from .stockstats_utils import _clean_dataframe


# Same indicator descriptions used by y_finance.py — keep in sync
_INDICATOR_DESCRIPTIONS = {
    "close_50_sma": (
        "50 SMA: A medium-term trend indicator. "
        "Usage: Identify trend direction and serve as dynamic support/resistance. "
        "Tips: It lags price; combine with faster indicators for timely signals."
    ),
    "close_200_sma": (
        "200 SMA: A long-term trend benchmark. "
        "Usage: Confirm overall market trend and identify golden/death cross setups. "
        "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
    ),
    "close_10_ema": (
        "10 EMA: A responsive short-term average. "
        "Usage: Capture quick shifts in momentum and potential entry points. "
        "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
    ),
    "macd": (
        "MACD: Computes momentum via differences of EMAs. "
        "Usage: Look for crossovers and divergence as signals of trend changes. "
        "Tips: Confirm with other indicators in low-volatility or sideways markets."
    ),
    "macds": (
        "MACD Signal: An EMA smoothing of the MACD line. "
        "Usage: Use crossovers with the MACD line to trigger trades. "
        "Tips: Should be part of a broader strategy to avoid false positives."
    ),
    "macdh": (
        "MACD Histogram: Shows the gap between the MACD line and its signal. "
        "Usage: Visualize momentum strength and spot divergence early. "
        "Tips: Can be volatile; complement with additional filters in fast-moving markets."
    ),
    "rsi": (
        "RSI: Measures momentum to flag overbought/oversold conditions. "
        "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
        "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
    ),
    "boll": (
        "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
        "Usage: Acts as a dynamic benchmark for price movement. "
        "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
    ),
    "boll_ub": (
        "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
        "Usage: Signals potential overbought conditions and breakout zones. "
        "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
    ),
    "boll_lb": (
        "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
        "Usage: Indicates potential oversold conditions. "
        "Tips: Use additional analysis to avoid false reversal signals."
    ),
    "atr": (
        "ATR: Averages true range to measure volatility. "
        "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
        "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
    ),
    "vwma": (
        "VWMA: A moving average weighted by volume. "
        "Usage: Confirm trends by integrating price action with volume data. "
        "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
    ),
    "mfi": (
        "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
        "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
        "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
    ),
}


def _fetch_and_cache_daily(ts_code: str, cache_dir: str) -> pd.DataFrame:
    """Fetch 15 years of daily data from Tushare, with CSV caching."""
    today = pd.Timestamp.today()
    start_date = today - pd.DateOffset(years=15)
    start_str = start_date.strftime("%Y%m%d")
    end_str = today.strftime("%Y%m%d")

    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(
        cache_dir,
        f"{ts_code}-Tushare-data-{start_date.strftime('%Y-%m-%d')}-{today.strftime('%Y-%m-%d')}.csv",
    )

    if os.path.exists(cache_file):
        return pd.read_csv(cache_file, on_bad_lines="skip")

    pro = get_pro_api()

    # Tushare limits single query to ~5000 rows; fetch in yearly chunks
    frames = []
    cursor = pd.to_datetime(end_str)
    chunk_start = pd.to_datetime(start_str)
    while cursor > chunk_start:
        seg_start = max(cursor - pd.DateOffset(years=2), chunk_start)
        df_chunk = tushare_api_call(
            pro.daily,
            ts_code=ts_code,
            start_date=seg_start.strftime("%Y%m%d"),
            end_date=cursor.strftime("%Y%m%d"),
        )
        if df_chunk is not None and not df_chunk.empty:
            frames.append(df_chunk)
        cursor = seg_start - pd.DateOffset(days=1)

    if not frames:
        return pd.DataFrame()

    data = pd.concat(frames, ignore_index=True)
    data = data.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")

    # Rename to framework-standard columns and convert units
    result = data[["trade_date", "open", "high", "low", "close", "vol"]].copy()
    result.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
    result["Volume"] = (result["Volume"] * 100).astype(int)
    result["Date"] = result["Date"].apply(from_tushare_date)

    result.to_csv(cache_file, index=False)
    return result


def get_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
    interval: str = "daily",
    time_period: int = 14,
    series_type: str = "close",
) -> str:
    """Compute technical indicators for A-shares using Tushare data + stockstats."""
    from stockstats import wrap
    from .config import get_config

    indicator = indicator.lower()
    if indicator not in _INDICATOR_DESCRIPTIONS:
        raise ValueError(
            f"Indicator {indicator} is not supported. "
            f"Please choose from: {list(_INDICATOR_DESCRIPTIONS.keys())}"
        )

    ts_code = normalize_ts_code(symbol)
    config = get_config()
    cache_dir = config.get("data_cache_dir", "data")

    # Fetch (or load cached) long-history daily data
    raw = _fetch_and_cache_daily(ts_code, cache_dir)
    if raw.empty:
        return f"No historical data available for {symbol} from Tushare."

    data = _clean_dataframe(raw.copy())
    df = wrap(data)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

    # Trigger stockstats indicator calculation
    df[indicator]

    # Build date-value lookup
    indicator_map = {}
    for _, row in df.iterrows():
        val = row[indicator]
        indicator_map[row["Date"]] = "N/A" if pd.isna(val) else str(val)

    # Generate output for the requested window
    end_date = curr_date
    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_dt - relativedelta(days=look_back_days)

    lines = []
    dt = curr_dt
    while dt >= before:
        ds = dt.strftime("%Y-%m-%d")
        val = indicator_map.get(ds, "N/A: Not a trading day (weekend or holiday)")
        lines.append(f"{ds}: {val}")
        dt -= relativedelta(days=1)

    result_str = (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {end_date}:\n\n"
        + "\n".join(lines)
        + "\n\n"
        + _INDICATOR_DESCRIPTIONS.get(indicator, "No description available.")
    )
    return result_str
