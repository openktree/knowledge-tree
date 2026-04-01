"""Tests for email base classes."""

import pytest

from kt_providers.email_base import EmailMessage, EmailProvider


def test_email_message_defaults() -> None:
    msg = EmailMessage(to="user@example.com", subject="Hi", html="<p>Hello</p>")
    assert msg.to == "user@example.com"
    assert msg.subject == "Hi"
    assert msg.html == "<p>Hello</p>"
    assert msg.from_address is None


def test_email_message_with_from() -> None:
    msg = EmailMessage(
        to="user@example.com",
        subject="Hi",
        html="<p>Hello</p>",
        from_address="sender@example.com",
    )
    assert msg.from_address == "sender@example.com"


def test_email_provider_is_abstract() -> None:
    """EmailProvider cannot be instantiated directly."""
    with pytest.raises(TypeError):
        EmailProvider()  # type: ignore[abstract]
