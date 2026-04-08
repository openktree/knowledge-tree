"""Unit tests for FetchResult / FetchAttempt dataclasses."""

from kt_providers.fetch.types import MIN_EXTRACTED_LENGTH, FetchAttempt, FetchResult


def test_fetch_result_success_with_content():
    r = FetchResult(uri="https://example.com", content="x" * MIN_EXTRACTED_LENGTH)
    assert r.success is True


def test_fetch_result_failure_short_content():
    r = FetchResult(uri="https://example.com", content="too short")
    assert r.success is False


def test_fetch_result_failure_no_content():
    r = FetchResult(uri="https://example.com", error="boom")
    assert r.success is False


def test_fetch_result_is_image_for_png():
    r = FetchResult(uri="https://example.com/x.png", content_type="image/png")
    assert r.is_image is True


def test_fetch_result_not_image_for_html():
    r = FetchResult(uri="https://example.com/x.html", content_type="text/html")
    assert r.is_image is False


def test_fetch_attempt_to_dict_round_trip():
    a = FetchAttempt(provider_id="httpx", success=True, error=None, elapsed_ms=42)
    payload = a.to_dict()
    assert payload == {
        "provider_id": "httpx",
        "success": True,
        "error": None,
        "elapsed_ms": 42,
    }
