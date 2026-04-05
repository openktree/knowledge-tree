"""Tests for _ssl_connect_args helper in session module."""

import ssl

from kt_db.session import _ssl_connect_args


def test_empty_sslmode_returns_empty():
    assert _ssl_connect_args("") == {}


def test_require_sslmode():
    result = _ssl_connect_args("require")
    assert "ssl" in result
    ctx = result["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE


def test_verify_full_sslmode():
    result = _ssl_connect_args("verify-full")
    assert "ssl" in result
    ctx = result["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True  # default for verify-full


def test_verify_ca_sslmode():
    result = _ssl_connect_args("verify-ca")
    assert "ssl" in result
    ctx = result["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False


def test_unknown_sslmode_returns_empty():
    assert _ssl_connect_args("disable") == {}
