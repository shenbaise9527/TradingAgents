"""Unit tests for tushare_common utility functions."""

import pytest

from tradingagents.dataflows.tushare_common import (
    TushareNotConfiguredError,
    TushareRateLimitError,
    from_tushare_date,
    get_api_token,
    normalize_ts_code,
    shanghai_now,
    to_tushare_date,
    tushare_api_call,
)


class TestDateConversion:
    def test_to_tushare_date(self):
        assert to_tushare_date("2024-03-15") == "20240315"

    def test_to_tushare_date_jan(self):
        assert to_tushare_date("2023-01-01") == "20230101"

    def test_from_tushare_date(self):
        assert from_tushare_date("20240315") == "2024-03-15"

    def test_from_tushare_date_jan(self):
        assert from_tushare_date("20230101") == "2023-01-01"

    def test_roundtrip(self):
        original = "2024-12-31"
        assert from_tushare_date(to_tushare_date(original)) == original


class TestNormalizeTsCode:
    def test_shanghai_6(self):
        assert normalize_ts_code("600000") == "600000.SH"

    def test_shenzhen_0(self):
        assert normalize_ts_code("000001") == "000001.SZ"

    def test_chinext_3(self):
        assert normalize_ts_code("300001") == "300001.SZ"

    def test_bse_8(self):
        assert normalize_ts_code("830001") == "830001.BJ"

    def test_bse_4(self):
        assert normalize_ts_code("430001") == "430001.BJ"

    def test_with_sh_suffix(self):
        assert normalize_ts_code("600000.SH") == "600000.SH"

    def test_with_sz_suffix(self):
        assert normalize_ts_code("000001.SZ") == "000001.SZ"

    def test_with_bj_suffix(self):
        assert normalize_ts_code("830001.BJ") == "830001.BJ"

    def test_lowercase_suffix(self):
        assert normalize_ts_code("600000.sh") == "600000.SH"

    def test_with_spaces(self):
        assert normalize_ts_code("  600000  ") == "600000.SH"

    def test_yahoo_ss_suffix_stripped(self):
        """Yahoo Finance uses .SS for Shanghai; we should convert properly."""
        result = normalize_ts_code("600000.SS")
        # .SS is not .SH/.SZ/.BJ, so it strips to bare code and re-maps
        assert result == "600000.SH"


class TestGetApiToken:
    """Token resolution supports both TUSHARE_API_TOKEN (primary) and the
    TUSHARE_TOKEN alias; missing both raises a typed not-configured error."""

    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("TUSHARE_API_TOKEN", raising=False)
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        with pytest.raises(ValueError, match="TUSHARE_API_TOKEN") as exc_info:
            get_api_token()
        assert "TUSHARE_TOKEN" in str(exc_info.value)

    def test_token_present(self, monkeypatch):
        monkeypatch.setenv("TUSHARE_API_TOKEN", "test_token_123")
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        assert get_api_token() == "test_token_123"

    def test_token_alias_fallback(self, monkeypatch):
        # ~/.zshrc-style export TUSHARE_TOKEN=... must be honoured when the
        # primary TUSHARE_API_TOKEN var is unset.
        monkeypatch.delenv("TUSHARE_API_TOKEN", raising=False)
        monkeypatch.setenv("TUSHARE_TOKEN", "alias_token_456")
        assert get_api_token() == "alias_token_456"

    def test_api_token_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("TUSHARE_API_TOKEN", "primary_token")
        monkeypatch.setenv("TUSHARE_TOKEN", "alias_token")
        assert get_api_token() == "primary_token"


class TestNormalizeTsCodeTyped:
    """normalize_ts_code raises NoMarketDataError for non-A-share symbols so the
    vendor router can fall back cleanly without wasting an API call."""

    def test_ss_bridged_to_sh(self):
        # Yahoo canonical .SS (Shanghai) maps to tushare's .SH.
        assert normalize_ts_code("600000.SS") == "600000.SH"

    def test_non_cn_raises_typed_error(self):
        from tradingagents.dataflows.errors import NoMarketDataError

        for sym in ("AAPL", "0700.HK", "^GSPC", "GC=F", "BTC-USD"):
            with pytest.raises(NoMarketDataError, match="not a China A-share"):
                normalize_ts_code(sym)

    def test_non_cn_error_carries_symbol(self):
        from tradingagents.dataflows.errors import NoMarketDataError

        with pytest.raises(NoMarketDataError) as exc_info:
            normalize_ts_code("0700.HK")
        assert exc_info.value.symbol == "0700.HK"


class TestTushareApiCallErrorMapping:
    """Rate/credit-limit messages -> TushareRateLimitError; entitlement
    (permission/points) messages -> TushareNotConfiguredError; anything else is
    re-raised unchanged."""

    def _call(self, message):
        def api_method(**kwargs):
            raise RuntimeError(message)

        return tushare_api_call(api_method)

    def test_rate_limit_keywords(self):
        for msg in (
            "抱歉，您每分钟最多访问该接口60次",
            "访问频率过快，请稍后再试",
            "rate limit exceeded, retry later",
        ):
            with pytest.raises(TushareRateLimitError, match="rate/credit"):
                self._call(msg)

    def test_entitlement_keywords(self):
        for msg in (
            "抱歉，您没有访问该接口的权限",
            "您的积分必须达到5000才能访问该接口",
        ):
            with pytest.raises(TushareNotConfiguredError, match="权限|授权|access"):
                self._call(msg)

    def test_unrelated_error_passes_through(self):
        with pytest.raises(RuntimeError, match="boom"):
            self._call("boom: something else broke")


class TestShanghaiNow:
    def test_returns_aware_dt_at_utc8(self):
        now = shanghai_now()
        assert now.tzinfo is not None
        assert now.utcoffset().total_seconds() == 8 * 3600

    def test_close_to_system_time(self):
        from datetime import datetime, timezone

        now = shanghai_now()
        utc_now = datetime.now(timezone.utc)
        # Both are aware instants; Shanghai wall time is UTC+8, so the two
        # instants must agree within a few seconds regardless of offset.
        assert abs((now - utc_now).total_seconds()) < 600


class TestProApiImportGuard:
    def test_missing_package_raises_not_configured(self, monkeypatch):
        import sys

        import tradingagents.dataflows.tushare_common as tc

        monkeypatch.setattr(tc, "_pro_api", None)
        monkeypatch.setitem(sys.modules, "tushare", None)
        with pytest.raises(TushareNotConfiguredError, match="tushare"):
            tc.get_pro_api()
