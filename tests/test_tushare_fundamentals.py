"""Mock-based contract tests for the tushare fundamentals vendor methods.

All tushare API access is faked at the ``get_pro_api`` seam; no real network.
Classified failures (rate limit / entitlement) must propagate as typed errors
so the router can fall back, and truly-empty results raise NoMarketDataError
instead of returning prose the LLM could misread as facts.
"""

import pandas as pd
import pytest

from tradingagents.dataflows import tushare_fundamentals as tf
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.tushare_common import TushareRateLimitError


class _FakeMethod:
    def __init__(self, df, error):
        self.df = df
        self.error = error

    def __call__(self, **kwargs):
        if self.error is not None:
            raise self.error
        return self.df


class _FakePro:
    def __init__(self, frames, errors=None):
        self._frames = frames
        self._errors = errors or {}
        self.calls = {}

    def __getattr__(self, name):
        if name in self._errors:
            return _FakeMethod(pd.DataFrame(), self._errors[name])
        return _FakeMethod(self._frames.get(name, pd.DataFrame()), None)

    def track(self, name, **kwargs):
        self.calls.setdefault(name, []).append(kwargs)


BASIC = pd.DataFrame([{"ts_code": "600519.SH", "name": "贵州茅台", "industry": "白酒",
                       "area": "贵州", "market": "主板", "list_date": "20010827"}])
COMPANY = pd.DataFrame([{"chairman": "丁雄军", "employees": 26000,
                         "main_business": "茅台酒及系列酒的生产与销售"}])
DAILY_BASIC = pd.DataFrame([{"total_mv": 210000000, "pe_ttm": 30.5, "pe": 28.0,
                             "pb": 8.5, "dv_ttm": 1.5, "total_share": 1256197800,
                             "float_share": 1256197800, "turnover_rate": 0.3,
                             "volume_ratio": 1.2}])
FINA = pd.DataFrame([{"eps": 59.49, "roe": 30.0, "roa": 20.0,
                      "netprofit_margin": 50.0, "grossprofit_margin": 91.0,
                      "revenue_yoy": 15.0, "netprofit_yoy": 16.0,
                      "debt_to_assets": 20.0, "current_ratio": 3.0,
                      "quick_ratio": 2.5}])


@pytest.fixture
def fake_pro(monkeypatch):
    def install(frames=None, errors=None):
        pro = _FakePro(frames or {}, errors)
        monkeypatch.setattr(tf, "get_pro_api", lambda: pro)
        return pro

    return install


class TestFundamentals:
    def test_non_cn_raises_without_api(self, fake_pro, monkeypatch):
        monkeypatch.setattr(tf, "get_pro_api",
                            lambda: (_ for _ in ()).throw(AssertionError("no API")))
        with pytest.raises(NoMarketDataError, match="not a China A-share"):
            tf.get_fundamentals("AAPL", "2024-03-20")

    def test_formats_key_fields(self, fake_pro):
        fake_pro({"stock_company": COMPANY, "stock_basic": BASIC,
                  "daily_basic": DAILY_BASIC, "fina_indicator": FINA})
        out = tf.get_fundamentals("600519.SS", "2024-03-20")
        assert out.startswith("# Company Fundamentals for 600519.SH")
        assert "Name: 贵州茅台" in out
        assert "Industry: 白酒" in out
        assert "Chairman: 丁雄军" in out
        assert "Market Cap: 2100000000000" in out  # 万元 -> 元
        assert "EPS: 59.49" in out

    def test_empty_result_raises_no_data(self, fake_pro):
        fake_pro()  # every frame empty
        with pytest.raises(NoMarketDataError, match="no fundamentals"):
            tf.get_fundamentals("600519.SS", "2024-03-20")

    def test_rate_limit_propagates_typed(self, fake_pro):
        fake_pro(errors={"daily_basic": RuntimeError("抱歉，您每分钟最多访问该接口60次")})
        with pytest.raises(TushareRateLimitError):
            tf.get_fundamentals("600519.SS", "2024-03-20")


class TestStatements:
    def test_balance_sheet_formats_csv(self, fake_pro):
        sheet = pd.DataFrame([{"end_date": "20231231", "total_assets": 1e12,
                               "ts_code": "600519.SH"}])
        fake_pro({"balancesheet": sheet})
        out = tf.get_balance_sheet("600519.SS", "quarterly", "2024-03-20")
        assert out.startswith("# Balance Sheet data for 600519.SH (quarterly)")
        assert "end_date,20231231" in out or "20231231" in out

    def test_cashflow_and_income_non_cn_rejected(self, fake_pro):
        for fn in (tf.get_cashflow, tf.get_income_statement):
            with pytest.raises(NoMarketDataError, match="not a China A-share"):
                fn("AAPL", "quarterly", "2024-03-20")

    def test_empty_statements_raise_no_data(self, fake_pro):
        fake_pro()
        with pytest.raises(NoMarketDataError, match="no balance sheet"):
            tf.get_balance_sheet("600519.SS", "quarterly", "2024-03-20")
