"""Mock-based contract tests for the tushare technical-indicator vendor path.

The history fetch is stubbed at the ``fetch_daily_bars`` seam; the real
tushare API is never called. Point-in-time semantics (rows after the analysis
date must not feed indicator values) are asserted explicitly.
"""

import pandas as pd
import pytest

from tradingagents.dataflows import tushare_indicator
from tradingagents.dataflows.errors import NoMarketDataError


def _make_history(rows):
    """Build a daily frame with the given (YYYYMMDD, close) rows."""
    n = len(rows)
    return pd.DataFrame(
        {
            "ts_code": ["600000.SH"] * n,
            "trade_date": [r[0] for r in rows],
            "open": [c for _, c in rows],
            "high": [c * 1.01 for _, c in rows],
            "low": [c * 0.99 for _, c in rows],
            "close": [c for _, c in rows],
            "vol": [10000] * n,
            "amount": [c * 10000.0 for _, c in rows],
        }
    )


@pytest.fixture
def patch_history(monkeypatch):
    def _install(df):
        calls = []

        def fake(ts_code, start_date, end_date, adj="qfq"):
            calls.append((ts_code, start_date, end_date, adj))
            return df

        monkeypatch.setattr(tushare_indicator, "fetch_daily_bars", fake)
        return calls

    return _install


# 90 daily closes trending 10.0 -> 10.9 (simple enough for indicator math).
TRENDING = [(f"2024{i:02d}{d:02d}", 10.0 + i * 0.05 + d * 0.002)
            for i in range(1, 4) for d in range(1, 28)]


def _business_day_rows(start, end, close=10.0):
    """(YYYYMMDD, close) rows on business days in [start, end)."""
    days = pd.bdate_range(start=start, end=end)
    return [(d.strftime("%Y%m%d"), close) for d in days]


class TestGetIndicatorContract:
    def test_output_shape_matches_yfinance_tool(self, patch_history):
        patch_history(_make_history(TRENDING))
        out = tushare_indicator.get_indicator("600000.SS", "rsi", "2024-03-20", 10)
        assert out.startswith("## rsi values from 2024-03-10 to 2024-03-20:")
        # One line per calendar day in the window.
        assert "2024-03-20:" in out
        assert "2024-03-10:" in out
        # Description footer from the shared single source.
        assert "RSI: Measures momentum" in out
        # Every line is date: value, never a bare number.
        for line in out.splitlines():
            if line[:4].isdigit() and ":" in line:
                assert line[4] == "-"

    def test_non_cn_symbol_raises_without_fetch(self, patch_history):
        calls = patch_history(_make_history(TRENDING))
        with pytest.raises(NoMarketDataError, match="not a China A-share"):
            tushare_indicator.get_indicator("AAPL", "rsi", "2024-03-20", 10)
        assert calls == []

    def test_unsupported_indicator_raises_value_error(self, patch_history):
        with pytest.raises(ValueError, match="not supported"):
            tushare_indicator.get_indicator("600000.SS", "not_an_indicator", "2024-03-20", 10)

    def test_analysis_date_before_history_raises(self, patch_history):
        # History starts 2024-01-02; an analysis date before it has no rows.
        patch_history(_make_history(_business_day_rows("2024-01-02", "2024-02-28")))
        with pytest.raises(NoMarketDataError, match="on or before"):
            tushare_indicator.get_indicator("600000.SS", "rsi", "2023-12-31", 10)

    def test_future_rows_do_not_leak_into_indicator_values(self, patch_history):
        """Point-in-time: rows after the analysis date must be excluded before
        stockstats computes, otherwise the 'current' value sees the future."""
        # ~1y of business days at 10.0, then a 10x "future" spike (April+).
        base = _make_history(_business_day_rows("2023-01-02", "2024-02-28"))
        future = _make_history(_business_day_rows("2024-04-01", "2024-05-31", close=100.0))
        patch_history(pd.concat([base, future], ignore_index=True))
        out = tushare_indicator.get_indicator("600000.SS", "close_50_sma", "2024-02-15", 5)
        # With leakage the 50-SMA would be dragged toward 100; assert ~10.
        for line in out.splitlines():
            if line.startswith("2024-02-") and ":" in line and "N/A" not in line:
                value = float(line.split(":", 1)[1].strip())
                assert value < 20, f"future rows leaked into indicator: {line}"
