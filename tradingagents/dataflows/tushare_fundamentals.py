from typing import Annotated
from datetime import datetime

from .tushare_common import (
    get_pro_api,
    normalize_ts_code,
    to_tushare_date,
    tushare_api_call,
)


def _latest_quarter_end(ref_date: str | None = None) -> str:
    """Return the most recent quarter-end date as YYYYMMDD relative to ref_date."""
    if ref_date:
        dt = datetime.strptime(ref_date, "%Y-%m-%d")
    else:
        dt = datetime.now()
    month = dt.month
    year = dt.year
    if month >= 10:
        return f"{year}0930"
    elif month >= 7:
        return f"{year}0630"
    elif month >= 4:
        return f"{year}0331"
    else:
        return f"{year - 1}1231"


def _quarter_periods(ref_date: str | None, count: int = 8) -> list[str]:
    """Generate a list of recent quarter-end period strings (YYYYMMDD)."""
    quarter_ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    if ref_date:
        dt = datetime.strptime(ref_date, "%Y-%m-%d")
    else:
        dt = datetime.now()

    periods = []
    year = dt.year
    # Start from current quarter going backwards
    for y in range(year, year - 5, -1):
        for m, d in reversed(quarter_ends):
            qe = datetime(y, m, d)
            if qe <= dt:
                periods.append(qe.strftime("%Y%m%d"))
            if len(periods) >= count:
                return periods
    return periods


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date in yyyy-mm-dd format"] = None,
) -> str:
    """Get company fundamentals overview from Tushare."""
    ts_code = normalize_ts_code(ticker)
    pro = get_pro_api()

    try:
        # Company basic info
        company_df = tushare_api_call(
            pro.stock_company, ts_code=ts_code, fields=(
                "ts_code,chairman,manager,secretary,reg_capital,"
                "setup_date,province,city,introduction,website,"
                "main_business,employees"
            ),
        )

        # Stock basic info (name, industry, etc.)
        basic_df = tushare_api_call(
            pro.stock_basic, ts_code=ts_code, fields="ts_code,name,industry,area,market,list_date",
        )

        # Daily basic indicators (PE, PB, market cap, etc.)
        trade_date = to_tushare_date(curr_date) if curr_date else None
        if trade_date:
            daily_basic_df = tushare_api_call(
                pro.daily_basic, ts_code=ts_code, trade_date=trade_date,
            )
        else:
            daily_basic_df = tushare_api_call(
                pro.daily_basic, ts_code=ts_code, limit=1,
            )

        # Financial indicators
        period = _latest_quarter_end(curr_date)
        fina_df = tushare_api_call(
            pro.fina_indicator, ts_code=ts_code, period=period,
        )

        # Build key-value output
        fields = []

        if basic_df is not None and not basic_df.empty:
            row = basic_df.iloc[0]
            fields.append(("Name", row.get("name")))
            fields.append(("Industry", row.get("industry")))
            fields.append(("Area", row.get("area")))
            fields.append(("Market", row.get("market")))
            fields.append(("List Date", row.get("list_date")))

        if company_df is not None and not company_df.empty:
            crow = company_df.iloc[0]
            fields.append(("Chairman", crow.get("chairman")))
            fields.append(("Employees", crow.get("employees")))
            fields.append(("Main Business", crow.get("main_business")))

        if daily_basic_df is not None and not daily_basic_df.empty:
            drow = daily_basic_df.iloc[0]
            # total_mv is in 万元, convert to 元
            total_mv = drow.get("total_mv")
            if total_mv is not None:
                fields.append(("Market Cap", total_mv * 10000))
            fields.append(("PE Ratio (TTM)", drow.get("pe_ttm")))
            fields.append(("PE Ratio", drow.get("pe")))
            fields.append(("Price to Book", drow.get("pb")))
            fields.append(("Dividend Yield (TTM)", drow.get("dv_ttm")))
            fields.append(("Total Share", drow.get("total_share")))
            fields.append(("Float Share", drow.get("float_share")))
            fields.append(("Turnover Rate", drow.get("turnover_rate")))
            fields.append(("Volume Ratio", drow.get("volume_ratio")))

        if fina_df is not None and not fina_df.empty:
            frow = fina_df.iloc[0]
            fields.append(("EPS", frow.get("eps")))
            fields.append(("Return on Equity", frow.get("roe")))
            fields.append(("Return on Assets", frow.get("roa")))
            fields.append(("Net Profit Margin", frow.get("netprofit_margin")))
            fields.append(("Gross Profit Margin", frow.get("grossprofit_margin")))
            fields.append(("Revenue (YoY %)", frow.get("revenue_yoy")))
            fields.append(("Net Profit (YoY %)", frow.get("netprofit_yoy")))
            fields.append(("Debt to Asset Ratio", frow.get("debt_to_assets")))
            fields.append(("Current Ratio", frow.get("current_ratio")))
            fields.append(("Quick Ratio", frow.get("quick_ratio")))

        lines = []
        for label, value in fields:
            if value is not None:
                lines.append(f"{label}: {value}")

        header = f"# Company Fundamentals for {ts_code}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + "\n".join(lines)

    except Exception as e:
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in yyyy-mm-dd format"] = None,
) -> str:
    """Get balance sheet data from Tushare."""
    ts_code = normalize_ts_code(ticker)
    pro = get_pro_api()

    try:
        count = 8 if freq.lower() == "quarterly" else 5
        periods = _quarter_periods(curr_date, count)

        if freq.lower() == "annual":
            # Keep only year-end periods (12-31)
            periods = [p for p in periods if p[4:8] == "1231"]
            if not periods:
                periods = _quarter_periods(curr_date, 20)
                periods = [p for p in periods if p[4:8] == "1231"][:5]

        frames = []
        for period in periods:
            df = tushare_api_call(
                pro.balancesheet, ts_code=ts_code, period=period, report_type="1",
            )
            if df is not None and not df.empty:
                frames.append(df.head(1))

        if not frames:
            return f"No balance sheet data found for symbol '{ticker}'"

        data = _dedupe_concat(frames)
        csv_string = data.to_csv(index=False)

        header = f"# Balance Sheet data for {ts_code} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"


def get_cashflow(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in yyyy-mm-dd format"] = None,
) -> str:
    """Get cash flow data from Tushare."""
    ts_code = normalize_ts_code(ticker)
    pro = get_pro_api()

    try:
        count = 8 if freq.lower() == "quarterly" else 5
        periods = _quarter_periods(curr_date, count)

        if freq.lower() == "annual":
            periods = [p for p in periods if p[4:8] == "1231"]
            if not periods:
                periods = _quarter_periods(curr_date, 20)
                periods = [p for p in periods if p[4:8] == "1231"][:5]

        frames = []
        for period in periods:
            df = tushare_api_call(
                pro.cashflow, ts_code=ts_code, period=period, report_type="1",
            )
            if df is not None and not df.empty:
                frames.append(df.head(1))

        if not frames:
            return f"No cash flow data found for symbol '{ticker}'"

        data = _dedupe_concat(frames)
        csv_string = data.to_csv(index=False)

        header = f"# Cash Flow data for {ts_code} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"


def get_income_statement(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in yyyy-mm-dd format"] = None,
) -> str:
    """Get income statement data from Tushare."""
    ts_code = normalize_ts_code(ticker)
    pro = get_pro_api()

    try:
        count = 8 if freq.lower() == "quarterly" else 5
        periods = _quarter_periods(curr_date, count)

        if freq.lower() == "annual":
            periods = [p for p in periods if p[4:8] == "1231"]
            if not periods:
                periods = _quarter_periods(curr_date, 20)
                periods = [p for p in periods if p[4:8] == "1231"][:5]

        frames = []
        for period in periods:
            df = tushare_api_call(
                pro.income, ts_code=ts_code, period=period, report_type="1",
            )
            if df is not None and not df.empty:
                frames.append(df.head(1))

        if not frames:
            return f"No income statement data found for symbol '{ticker}'"

        data = _dedupe_concat(frames)
        csv_string = data.to_csv(index=False)

        header = f"# Income Statement data for {ts_code} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving income statement for {ticker}: {str(e)}"


def _dedupe_concat(frames: list) -> "pd.DataFrame":
    """Concatenate DataFrames and drop duplicate reporting periods."""
    import pandas as pd

    data = pd.concat(frames, ignore_index=True)
    if "end_date" in data.columns:
        data = data.drop_duplicates(subset=["end_date"]).sort_values(
            "end_date", ascending=False
        )
    return data
