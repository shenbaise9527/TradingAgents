from typing import Annotated
from datetime import datetime

from .tushare_common import (
    get_pro_api,
    normalize_ts_code,
    to_tushare_date,
    from_tushare_date,
    tushare_api_call,
)


def get_stock(
    symbol: Annotated[str, "ticker symbol of the company (e.g., 000001.SZ)"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Fetch daily OHLCV data from Tushare for A-shares."""
    ts_code = normalize_ts_code(symbol)
    ts_start = to_tushare_date(start_date)
    ts_end = to_tushare_date(end_date)

    pro = get_pro_api()
    df = tushare_api_call(
        pro.daily, ts_code=ts_code, start_date=ts_start, end_date=ts_end
    )

    if df is None or df.empty:
        return (
            f"No data found for symbol '{symbol}' between {start_date} and {end_date}"
        )

    # Sort by trade_date ascending (Tushare returns newest first)
    df = df.sort_values("trade_date").reset_index(drop=True)

    # Map columns to the framework's expected format
    result = df[["trade_date", "open", "high", "low", "close", "vol"]].copy()
    result.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]

    # Tushare vol is in "hands" (手 = 100 shares), convert to shares
    result["Volume"] = (result["Volume"] * 100).astype(int)

    # A-shares don't have a separate adjusted close; use close
    result["Adj Close"] = result["Close"]

    # Round prices to 2 decimal places
    for col in ["Open", "High", "Low", "Close", "Adj Close"]:
        result[col] = result[col].round(2)

    # Convert dates from YYYYMMDD to yyyy-mm-dd
    result["Date"] = result["Date"].apply(from_tushare_date)

    # Reorder columns to match yfinance output
    result = result[["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]]

    csv_string = result.to_csv(index=False)

    header = f"# Stock data for {ts_code} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(result)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    return header + csv_string
