import logging
from datetime import datetime
from typing import Annotated

from dateutil.relativedelta import relativedelta

from .config import get_config
from .date_window import in_window
from .tushare_common import (
    cst_to_utc,
    get_pro_api,
    normalize_ts_code,
    to_tushare_date,
    tushare_api_call,
)

logger = logging.getLogger(__name__)

# Tushare's news interface (src="sina") has no per-ticker endpoint, so ticker
# news is fetched from the general stream and filtered locally by stock code /
# company name. Content is trimmed to keep tool output small.
_CONTENT_CAP = 500


def _company_name(pro, ts_code: str) -> str | None:
    """Best-effort company name for local news filtering.

    Never fails the news call: an unreadable/lookup failure (permission on
    ``stock_basic``, transient error) only degrades filtering to code matches.
    """
    try:
        basic_df = tushare_api_call(
            pro.stock_basic, ts_code=ts_code, fields="ts_code,name",
        )
        if basic_df is not None and not basic_df.empty:
            return str(basic_df.iloc[0].get("name") or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — auxiliary lookup, see docstring
        logger.debug("Could not resolve company name for %s: %s", ts_code, exc)
    return None


def _article_matches(row, search_terms) -> bool:
    text = f"{row.get('title', '')}{row.get('content', '')}"
    return any(term in text for term in search_terms)


def _format_articles(articles, limit: int) -> str:
    """Render kept articles as markdown, deduplicated by title and capped."""
    seen = set()
    lines = []
    for art in articles:
        title = str(art.get("title", "")).strip()
        if not title or title in seen:
            continue
        seen.add(title)
        lines.append(f"### {title} (source: {art.get('src', 'sina')})")
        content = str(art.get("content", "")).strip()
        if content:
            lines.append(content[:_CONTENT_CAP])
        dt = art.get("datetime")
        if dt:
            lines.append(f"Date: {dt}")
        lines.append("")
        if len(seen) >= limit:
            break
    return "\n".join(lines)


def _news_rows_in_window(pro, ts_start: str, ts_end: str,
                         start_dt: datetime, end_dt: datetime, search_terms):
    """Fetch the general news stream and keep rows inside the window.

    Returns (kept_rows, total_in_window) — classified tushare failures
    propagate; the caller decides what a window with no kept rows means.
    """
    news_df = tushare_api_call(
        pro.news, src="sina", start_date=ts_start, end_date=ts_end,
    )
    if news_df is None or news_df.empty:
        return [], 0

    kept = []
    total_in_window = 0
    for _, row in news_df.iterrows():
        pub_dt = cst_to_utc(row.get("datetime"))
        if not in_window(pub_dt, start_dt, end_dt):
            continue
        total_in_window += 1
        if search_terms is None or _article_matches(row, search_terms):
            kept.append({
                "title": str(row.get("title", "")),
                "content": str(row.get("content", "")),
                "datetime": str(row.get("datetime", "")),
                "src": str(row.get("src", "sina")),
            })
    return kept, total_in_window


def get_news(
    ticker: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Get company-related news from Tushare.

    Tushare's news API does not support ticker-level filtering directly, so the
    general stream is fetched and filtered locally by company name / code, then
    trimmed to the requested window with the same look-ahead-safe semantics as
    the yfinance path (``date_window.in_window``, CST timestamps -> UTC).

    An empty window reports plainly ("No news found ...") rather than padding
    the report with unrelated headlines.
    """
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")
    ts_code = normalize_ts_code(ticker)
    bare_code = ts_code.split(".")[0]
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    pro = get_pro_api()
    name = _company_name(pro, ts_code)
    search_terms = [bare_code, ts_code]
    if name:
        search_terms.append(name)

    kept, _ = _news_rows_in_window(
        pro,
        to_tushare_date(start_date),
        to_tushare_date(end_date),
        start_dt, end_dt,
        search_terms,
    )

    limit = get_config().get("news_article_limit", 20)
    if not kept:
        return (
            f"No news found for {ts_code} between {start_date} and {end_date}. "
            f"Tushare news coverage for individual A-share stocks may be limited."
        )

    body = _format_articles(kept, limit)
    return f"## {ts_code} News, from {start_date} to {end_date}:\n\n{body}"


def get_global_news(
    curr_date: Annotated[str, "current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int | None, "days to look back; None = config default"] = None,
    limit: Annotated[int | None, "max articles; None = config default"] = None,
) -> str:
    """Get general financial/market news from Tushare (look-ahead safe)."""
    config = get_config()
    if look_back_days is None:
        look_back_days = config.get("global_news_lookback_days", 7)
    if limit is None:
        limit = config.get("global_news_article_limit", 10)

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - relativedelta(days=look_back_days)

    pro = get_pro_api()
    kept, _ = _news_rows_in_window(
        pro,
        to_tushare_date(start_dt.strftime("%Y-%m-%d")),
        to_tushare_date(curr_date),
        start_dt, end_dt,
        search_terms=None,  # global stream: keep everything in-window
    )

    if not kept:
        return f"No global news found between {start_dt.strftime('%Y-%m-%d')} and {curr_date}"

    body = _format_articles(kept, limit)
    return (
        f"## Global Market News, from {start_dt.strftime('%Y-%m-%d')} to "
        f"{curr_date}:\n\n{body}"
    )


def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> str:
    """Get major shareholder trading activity from Tushare.

    A-shares don't have US-style SEC insider transaction filings; this returns
    major-shareholder increase/decrease disclosures (大股东增减持). Absence is
    normal (many stocks have none), so it is reported as plain text exactly
    like the yfinance insider path — not as a data error.
    """
    ts_code = normalize_ts_code(ticker)
    pro = get_pro_api()

    df = tushare_api_call(pro.stk_holdertrade, ts_code=ts_code)
    if df is None or df.empty:
        return (
            f"No major shareholder trading data found for {ts_code}. "
            f"Note: A-shares use shareholder trading disclosure (大股东增减持) "
            f"instead of US-style insider transaction reporting."
        )

    csv_string = df.to_csv(index=False)

    header = f"# Major Shareholder Trading data for {ts_code}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += "# Note: This shows major shareholder increases/decreases in holdings\n\n"

    return header + csv_string
