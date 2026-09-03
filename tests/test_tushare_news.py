"""Mock-based contract tests for the tushare news / insider vendor methods.

All tushare API access is faked at the ``get_pro_api`` seam. Look-ahead-safe
window filtering (``date_window.in_window``, CST -> UTC) is asserted: articles
outside [start, end] must never reach the report, and classified vendor errors
propagate typed so the router can act on them.
"""

import pandas as pd
import pytest

from tradingagents.dataflows import tushare_news as tn
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
    def __init__(self, frames=None, errors=None):
        self._frames = frames or {}
        self._errors = errors or {}

    def __getattr__(self, name):
        if name in self._errors:
            return _FakeMethod(pd.DataFrame(), self._errors[name])
        return _FakeMethod(self._frames.get(name, pd.DataFrame()), None)


def _news_row(title, content, dt, src="sina"):
    return {"title": title, "content": content, "datetime": dt, "src": src}


@pytest.fixture
def fake_pro(monkeypatch):
    def install(frames=None, errors=None):
        pro = _FakePro(frames, errors)
        monkeypatch.setattr(tn, "get_pro_api", lambda: pro)
        return pro

    return install


NEWS_DF = pd.DataFrame(
    [
        _news_row("贵州茅台600519业绩超预期", "净利润增长16%", "2024-01-03 09:30:00"),
        _news_row("央行降准释放流动性", "宏观政策", "2024-01-03 09:00:00"),  # unrelated
        _news_row("600519 年内新高", "盘中突破", "2024-02-01 10:00:00"),     # outside window
        _news_row("白酒板块回暖", "板块行情", "2024-01-04 15:05:00"),          # matches? no code/name
    ]
)
BASIC = pd.DataFrame([{"ts_code": "600519.SH", "name": "贵州茅台"}])


class TestGetNews:
    def test_non_cn_raises_without_api(self, fake_pro):
        with pytest.raises(NoMarketDataError, match="not a China A-share"):
            tn.get_news("AAPL", "2024-01-02", "2024-01-05")

    def test_invalid_dates_raise(self, fake_pro):
        with pytest.raises(ValueError):
            tn.get_news("600519.SS", "bad", "2024-01-05")

    def test_filters_by_term_and_window(self, fake_pro):
        fake_pro({"news": NEWS_DF, "stock_basic": BASIC})
        out = tn.get_news("600519.SS", "2024-01-02", "2024-01-05")
        assert "## 600519.SH News, from 2024-01-02 to 2024-01-05" in out
        assert "贵州茅台600519业绩超预期" in out
        # Unrelated, out-of-window, and non-matching articles must not appear.
        assert "央行降准" not in out
        assert "年内新高" not in out
        assert "白酒板块回暖" not in out

    def test_no_articles_in_window_reports_plainly(self, fake_pro):
        only_future = pd.DataFrame(
            [_news_row("未来消息", "x", "2024-03-01 09:00:00")]
        )
        fake_pro({"news": only_future, "stock_basic": BASIC})
        out = tn.get_news("600519.SS", "2024-01-02", "2024-01-05")
        assert out.startswith("No news found for 600519.SH between")

    def test_rate_limit_propagates_typed(self, fake_pro):
        fake_pro(errors={"news": RuntimeError("每分钟最多访问该接口60次")})
        with pytest.raises(TushareRateLimitError):
            tn.get_news("600519.SS", "2024-01-02", "2024-01-05")


class TestGetGlobalNews:
    def test_none_defaults_use_config_and_window(self, fake_pro):
        fake_pro({"news": NEWS_DF})
        out = tn.get_global_news("2024-01-05", None, None)
        assert out.startswith("## Global Market News, from 2023-12-29 to 2024-01-05")
        assert "贵州茅台600519业绩超预期" in out  # in 7-day window, term-agnostic
        assert "年内新高" not in out              # 2024-02-01 outside window

    def test_honest_empty_when_nothing_in_window(self, fake_pro):
        stale = pd.DataFrame([_news_row("旧闻", "x", "2023-01-01 09:00:00")])
        fake_pro({"news": stale})
        out = tn.get_global_news("2024-01-05", None, None)
        assert out.startswith("No global news found between")


class TestInsider:
    def test_formats_header_and_csv(self, fake_pro):
        trades = pd.DataFrame([{"ts_code": "600519.SH", "ann_date": "20240320",
                                "holder_name": "茅台集团", "in_de": "DE",
                                "change_vol": 100000}])
        fake_pro({"stk_holdertrade": trades})
        out = tn.get_insider_transactions("600519.SS")
        assert out.startswith("# Major Shareholder Trading data for 600519.SH")

    def test_empty_is_plain_prose_not_error(self, fake_pro):
        fake_pro()
        out = tn.get_insider_transactions("600519.SS")
        assert "No major shareholder trading data" in out

    def test_non_cn_raises(self, fake_pro):
        with pytest.raises(NoMarketDataError, match="not a China A-share"):
            tn.get_insider_transactions("AAPL")
