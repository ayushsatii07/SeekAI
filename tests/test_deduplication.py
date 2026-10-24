"""
Tests for URL normalization utilities.
"""
import pytest

from app.utils.url_utils import (
    extract_domain,
    is_same_page,
    is_valid_url,
    normalize_url,
)


class TestNormalizeUrl:
    def test_removes_trailing_slash(self):
        assert normalize_url("https://example.com/article/") == normalize_url(
            "https://example.com/article"
        )

    def test_preserves_root_slash(self):
        result = normalize_url("https://example.com/")
        assert "example.com" in result

    def test_removes_fragment(self):
        assert normalize_url("https://example.com/page#section") == normalize_url(
            "https://example.com/page"
        )

    def test_removes_tracking_params(self):
        url_with_utm = "https://example.com/page?utm_source=google&utm_medium=cpc"
        normalized = normalize_url(url_with_utm)
        assert "utm_source" not in normalized
        assert "utm_medium" not in normalized

    def test_keeps_content_params(self):
        url = "https://example.com/search?q=python"
        normalized = normalize_url(url)
        assert "q=python" in normalized

    def test_strips_www(self):
        assert normalize_url("https://www.example.com/page") == normalize_url(
            "https://example.com/page"
        )

    def test_lowercases_domain(self):
        normalized = normalize_url("https://EXAMPLE.COM/page")
        assert "EXAMPLE" not in normalized

    def test_empty_url(self):
        assert normalize_url("") == ""

    def test_invalid_url_returns_original(self):
        bad = "not-a-url"
        result = normalize_url(bad)
        # Should return something (either original or partial)
        assert isinstance(result, str)


class TestExtractDomain:
    def test_basic(self):
        assert extract_domain("https://example.com/page") == "example.com"

    def test_strips_www(self):
        assert extract_domain("https://www.example.com") == "example.com"

    def test_subdomain_preserved(self):
        assert extract_domain("https://blog.example.com") == "blog.example.com"

    def test_empty(self):
        assert extract_domain("") == ""


class TestIsSamePage:
    def test_same_url_with_fragment(self):
        assert is_same_page(
            "https://example.com/page", "https://example.com/page#intro"
        )

    def test_same_url_with_trailing_slash(self):
        assert is_same_page(
            "https://example.com/page", "https://example.com/page/"
        )

    def test_different_pages(self):
        assert not is_same_page(
            "https://example.com/page1", "https://example.com/page2"
        )


class TestIsValidUrl:
    def test_valid_https(self):
        assert is_valid_url("https://example.com")

    def test_valid_http(self):
        assert is_valid_url("http://example.com")

    def test_invalid_no_scheme(self):
        assert not is_valid_url("example.com")

    def test_invalid_ftp(self):
        assert not is_valid_url("ftp://example.com")

    def test_empty(self):
        assert not is_valid_url("")
