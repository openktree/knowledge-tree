"""Tests for email provider factory."""

from unittest.mock import MagicMock

from kt_providers.email_factory import create_email_provider
from kt_providers.email_resend import ResendEmailProvider


def _make_settings(**overrides) -> MagicMock:  # type: ignore[no-untyped-def]
    defaults = {
        "email_enabled": False,
        "email_provider": "resend",
        "email_from_address": "noreply@example.com",
        "resend_api_key": "re_test_key",
    }
    defaults.update(overrides)
    settings = MagicMock()
    for k, v in defaults.items():
        setattr(settings, k, v)
    return settings


def test_returns_none_when_disabled() -> None:
    settings = _make_settings(email_enabled=False)
    assert create_email_provider(settings) is None


def test_returns_resend_when_enabled() -> None:
    settings = _make_settings(email_enabled=True)
    provider = create_email_provider(settings)
    assert isinstance(provider, ResendEmailProvider)


def test_returns_none_when_no_api_key() -> None:
    settings = _make_settings(email_enabled=True, resend_api_key="")
    assert create_email_provider(settings) is None


def test_returns_none_for_unknown_provider() -> None:
    settings = _make_settings(email_enabled=True, email_provider="sendgrid")
    assert create_email_provider(settings) is None
