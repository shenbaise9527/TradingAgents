"""Unit tests for tushare_common utility functions."""

import os
import pytest

from tradingagents.dataflows.tushare_common import (
    to_tushare_date,
    from_tushare_date,
    normalize_ts_code,
    get_api_token,
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
    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("TUSHARE_API_TOKEN", raising=False)
        with pytest.raises(ValueError, match="TUSHARE_API_TOKEN"):
            get_api_token()

    def test_token_present(self, monkeypatch):
        monkeypatch.setenv("TUSHARE_API_TOKEN", "test_token_123")
        assert get_api_token() == "test_token_123"
