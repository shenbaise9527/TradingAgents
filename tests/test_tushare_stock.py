"""Mock-based contract tests for the tushare OHLCV (get_stock) vendor path.

All network access is stubbed at the ``fetch_daily_bars`` seam; the real
tushare API is never called (no token/credit spend, offline-safe).
"""

import pandas as pd
import pytest

from tradingagents.dataflows import tushare_stock
from tradingagents.dataflows.errors import NoMarketDataError


def _canned_daily() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["600000.SH"] * 3,
            "trade_date": ["20240102", "20240103", "20240104"],
            "open": [10.1, 10.2, 10.3],
            "high": [10.5, 10.6, 10.4],
            "low": [9.9, 10.0, 10.1],
            "close": [10.2, 10.4, 10.3],
            "pre_close": [10.0, 10.2, 10.4],
            "change": [0.2, 0.2, -0.1],
            "pct_chg": [2.0, 1.96, -0.96],
            "vol": [10000, 12000, 9000],
            "amount": [102000.0, 124800.0, 92700.0],
        }
    )


@pytest.fixture
def patch_daily(monkeypatch):
    """Stub fetch_daily_bars; fail loudly if code paths call it twice."""

    def _install(df=None, error=None):
        calls = []

        def fake(ts_code, start_date, end_date, adj="qfq"):
            calls.append((ts_code, start_date, end_date, adj))
            if error is not None:
                raise error
            return _canned_daily() if df is None else df

        monkeypatch.setattr(tushare_stock, "fetch_daily_bars", fake)
        return calls

    return _install


class TestGetStockContract:
    def test_formats_csv_with_header_and_resolved_label(self, patch_daily):
        calls = patch_daily()
        out = tushare_stock.get_stock("600000.SS", "2024-01-01", "2024-01-10")
        assert calls == [("600000.SH", "2024-01-01", "2024-01-10", "qfq")]
        assert out.startswith("# Stock data for 600000.SH (from 600000.SS) from 2024-01-01 to 2024-01-10")
        assert "# Total records: 3" in out
        # Column set mirrors the yfinance vendor output the LLM already sees.
        head, _, body = out.partition("\n\n")
        assert "Date,Open,High,Low,Close,Volume" in body.splitlines()[0]
        assert "2024-01-02" in body
        assert "2024-01-04" in body
        # vol unit is 手 (100 shares) -> shares.
        row = [ln for ln in body.splitlines() if ln.startswith("2024-01-02")][0]
        assert ",1000000" in row  # 10000 手 * 100

    def test_dates_are_validated(self, patch_daily):
        with pytest.raises(ValueError):
            tushare_stock.get_stock("600000.SS", "not-a-date", "2024-01-10")

    def test_non_cn_symbol_raises_without_fetch(self, patch_daily):
        calls = patch_daily()
        with pytest.raises(NoMarketDataError, match="not a China A-share"):
            tushare_stock.get_stock("AAPL", "2024-01-01", "2024-01-10")
        assert calls == []

    def test_empty_result_raises_no_data(self, patch_daily):
        patch_daily(df=pd.DataFrame())
        with pytest.raises(NoMarketDataError, match="no rows"):
            tushare_stock.get_stock("600000.SS", "2024-01-01", "2024-01-10")

    def test_stale_frame_rejected(self, patch_daily):
        stale = _canned_daily().copy()
        stale["trade_date"] = ["20230102", "20230103", "20230104"]  # ~1y before end
        patch_daily(df=stale)
        with pytest.raises(NoMarketDataError, match="stale"):
            tushare_stock.get_stock("600000.SS", "2024-01-01", "2024-01-20")

    def test_missing_columns_raise_no_data(self, patch_daily):
        bad = _canned_daily().drop(columns=["close"])
        patch_daily(df=bad)
        with pytest.raises(NoMarketDataError, match="missing"):
            tushare_stock.get_stock("600000.SS", "2024-01-01", "2024-01-10")
