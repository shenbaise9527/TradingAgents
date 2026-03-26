from typing import Annotated
from datetime import datetime
from dateutil.relativedelta import relativedelta

from .tushare_common import (
    get_pro_api,
    normalize_ts_code,
    to_tushare_date,
    from_tushare_date,
    tushare_api_call,
)


def get_news(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Get company-related news from Tushare.

    Tushare's news API does not support ticker-level filtering directly,
    so we fetch general financial news and filter locally by company name/code.
    """
    ts_code = normalize_ts_code(ticker)
    bare_code = ts_code.split(".")[0]
    pro = get_pro_api()

    ts_start = to_tushare_date(start_date)
    ts_end = to_tushare_date(end_date)

    # Try to get the company name for local filtering
    company_name = None
    try:
        basic_df = tushare_api_call(
            pro.stock_basic, ts_code=ts_code, fields="ts_code,name",
        )
        if basic_df is not None and not basic_df.empty:
            company_name = basic_df.iloc[0].get("name")
    except Exception:
        pass

    # Fetch news from Sina source
    try:
        news_df = tushare_api_call(
            pro.news, src="sina", start_date=ts_start, end_date=ts_end,
        )
    except Exception:
        news_df = None

    articles = []
    if news_df is not None and not news_df.empty:
        # Filter by company name or stock code appearing in title/content
        search_terms = [bare_code, ts_code]
        if company_name:
            search_terms.append(company_name)

        for _, row in news_df.iterrows():
            title = str(row.get("title", ""))
            content = str(row.get("content", ""))
            text = title + content

            if any(term in text for term in search_terms):
                articles.append({
                    "title": title,
                    "content": content[:500],
                    "datetime": str(row.get("datetime", "")),
                    "source": str(row.get("src", "sina")),
                })

        # If no ticker-specific results, include top general news as context
        if not articles:
            for _, row in news_df.head(10).iterrows():
                articles.append({
                    "title": str(row.get("title", "")),
                    "content": str(row.get("content", ""))[:500],
                    "datetime": str(row.get("datetime", "")),
                    "source": str(row.get("src", "sina")),
                })

    if not articles:
        return (
            f"No news found for {ts_code} between {start_date} and {end_date}. "
            f"Tushare news coverage for individual A-share stocks may be limited."
        )

    # Format as markdown, matching yfinance output style
    lines = [f"## {ts_code} News, from {start_date} to {end_date}:\n"]
    for art in articles:
        lines.append(f"### {art['title']} (source: {art['source']})")
        if art["content"]:
            lines.append(art["content"])
        if art["datetime"]:
            lines.append(f"Date: {art['datetime']}")
        lines.append("")

    return "\n".join(lines)


def get_global_news(
    curr_date: Annotated[str, "current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "how many days to look back"] = 7,
    limit: Annotated[int, "maximum number of articles to return"] = 10,
) -> str:
    """Get general financial/market news from Tushare."""
    pro = get_pro_api()

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - relativedelta(days=look_back_days)
    ts_start = to_tushare_date(start_dt.strftime("%Y-%m-%d"))
    ts_end = to_tushare_date(curr_date)

    try:
        news_df = tushare_api_call(
            pro.news, src="sina", start_date=ts_start, end_date=ts_end,
        )
    except Exception:
        news_df = None

    if news_df is None or news_df.empty:
        return f"No global news found from {start_dt.strftime('%Y-%m-%d')} to {curr_date}."

    # Take top N articles
    news_df = news_df.head(limit)

    lines = [
        f"## Global Market News, from {start_dt.strftime('%Y-%m-%d')} to {curr_date}:\n"
    ]
    for _, row in news_df.iterrows():
        title = str(row.get("title", ""))
        content = str(row.get("content", ""))[:500]
        source = str(row.get("src", "sina"))
        dt = str(row.get("datetime", ""))
        lines.append(f"### {title} (source: {source})")
        if content:
            lines.append(content)
        if dt:
            lines.append(f"Date: {dt}")
        lines.append("")

    return "\n".join(lines)


def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Get major shareholder trading activity from Tushare.

    A-shares don't have US-style SEC insider transaction filings.
    Instead, this returns major shareholder (大股东) increase/decrease data.
    """
    ts_code = normalize_ts_code(ticker)
    pro = get_pro_api()

    try:
        df = tushare_api_call(pro.stk_holdertrade, ts_code=ts_code)
    except Exception:
        df = None

    if df is None or df.empty:
        return (
            f"No major shareholder trading data found for {ts_code}. "
            f"Note: A-shares use shareholder trading disclosure (大股东增减持) "
            f"instead of US-style insider transaction reporting."
        )

    csv_string = df.to_csv(index=False)

    header = f"# Major Shareholder Trading data for {ts_code}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += f"# Note: This shows major shareholder increases/decreases in holdings\n\n"

    return header + csv_string
