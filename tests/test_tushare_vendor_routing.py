"""Routing integration for the tushare vendor (interface.route_to_vendor).

All vendor implementations are stubbed at the seam (tushare's
``fetch_daily_bars`` / the yfinance impl in ``VENDOR_METHODS``), so no real
network or API credits are spent.
"""

import copy

import pandas as pd
import pytest

import tradingagents.default_config as default_config
from tradingagents.dataflows import interface, tushare_stock
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import NoMarketDataError


@pytest.fixture(autouse=True)
def _isolated_config():
    set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))
    yield
    set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))


def _cn_daily() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 2,
            "trade_date": ["20240102", "20240103"],
            "open": [10.0, 10.1],
            "high": [10.4, 10.3],
            "low": [9.9, 10.0],
            "close": [10.2, 10.2],
            "vol": [5000, 6000],
            "amount": [51000.0, 61200.0],
        }
    )


class TestTushareRouting:
    def test_category_config_selects_tushare(self, monkeypatch):
        monkeypatch.setattr(tushare_stock, "fetch_daily_bars",
                            lambda *a, **k: _cn_daily())
        set_config({"data_vendors": {"core_stock_apis": "tushare"}})
        out = interface.route_to_vendor(
            "get_stock_data", "000001.SZ", "2024-01-01", "2024-01-10"
        )
        assert out.startswith("# Stock data for 000001.SZ from 2024-01-01 to 2024-01-10")

    def test_tool_vendors_override_beats_category(self, monkeypatch):
        monkeypatch.setattr(tushare_stock, "fetch_daily_bars",
                            lambda *a, **k: _cn_daily())
        # Category says yfinance, but the tool-level override forces tushare.
        set_config({
            "data_vendors": {"core_stock_apis": "yfinance"},
            "tool_vendors": {"get_stock_data": "tushare"},
        })
        out = interface.route_to_vendor(
            "get_stock_data", "000001.SZ", "2024-01-01", "2024-01-10"
        )
        assert out.startswith("# Stock data for 000001.SZ")

    def test_non_cn_symbol_under_tushare_only_emits_no_data_sentinel(
        self, monkeypatch
    ):
        def boom(*a, **k):  # pragma: no cover — must never be reached
            raise AssertionError("tushare fetch called for a non-CN symbol")

        monkeypatch.setattr(tushare_stock, "fetch_daily_bars", boom)
        set_config({"data_vendors": {"core_stock_apis": "tushare"}})
        out = interface.route_to_vendor(
            "get_stock_data", "AAPL", "2024-01-01", "2024-01-10"
        )
        assert out.startswith("NO_DATA_AVAILABLE")
        assert "not a China A-share" in out

    def test_fallback_to_next_vendor_on_clean_no_data(self, monkeypatch):
        """tushare reports no-data -> the configured next vendor serves it."""
        def no_data(*a, **k):
            raise NoMarketDataError("600519.SS", "600519.SH", "no rows")

        def fake_yfinance(*a, **k):
            return "# Stock data (yfinance fallback)\n\nDate,Open\n2024-01-02,10"

        monkeypatch.setattr(tushare_stock, "fetch_daily_bars", no_data)
        monkeypatch.setitem(
            interface.VENDOR_METHODS["get_stock_data"],
            "yfinance", fake_yfinance,
        )
        set_config({"data_vendors": {"core_stock_apis": "tushare,yfinance"}})
        out = interface.route_to_vendor(
            "get_stock_data", "600519.SS", "2024-01-01", "2024-01-10"
        )
        assert "yfinance fallback" in out

    def test_unconfigured_vendor_never_enters_chain(self, monkeypatch):
        """Default config (yfinance) must not invoke tushare at all."""
        def boom(*a, **k):  # pragma: no cover
            raise AssertionError("tushare must not be in the default chain")

        monkeypatch.setattr(tushare_stock, "fetch_daily_bars", boom)
        # Default data_vendors.core_stock_apis == "yfinance": replace the
        # yfinance impl so no real network is needed.
        monkeypatch.setitem(
            interface.VENDOR_METHODS["get_stock_data"],
            "yfinance",
            lambda *a, **k: "# Stock data (default yfinance)\n\nDate,Open\n2024-01-02,10",
        )
        out = interface.route_to_vendor(
            "get_stock_data", "AAPL", "2024-01-01", "2024-01-10"
        )
        assert "default yfinance" in out
