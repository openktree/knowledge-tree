"""Tests for ResendEmailProvider."""

import httpx
import pytest
import respx

from kt_providers.email_base import EmailMessage
from kt_providers.email_resend import ResendEmailProvider


@pytest.fixture
def provider() -> ResendEmailProvider:
    return ResendEmailProvider(api_key="re_test_key", default_from="noreply@example.com")


@pytest.fixture
def message() -> EmailMessage:
    return EmailMessage(to="user@example.com", subject="Test", html="<p>Hi</p>")


@respx.mock
async def test_send_email_success(provider: ResendEmailProvider, message: EmailMessage) -> None:
    route = respx.post("https://api.resend.com/emails").mock(return_value=httpx.Response(200, json={"id": "msg_123"}))
    await provider.send_email(message)
    assert route.called
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer re_test_key"


@respx.mock
async def test_send_email_uses_default_from(provider: ResendEmailProvider, message: EmailMessage) -> None:
    route = respx.post("https://api.resend.com/emails").mock(return_value=httpx.Response(200, json={"id": "msg_123"}))
    await provider.send_email(message)
    import json

    body = json.loads(route.calls[0].request.content)
    assert body["from"] == "noreply@example.com"


@respx.mock
async def test_send_email_override_from(provider: ResendEmailProvider) -> None:
    route = respx.post("https://api.resend.com/emails").mock(return_value=httpx.Response(200, json={"id": "msg_123"}))
    msg = EmailMessage(
        to="user@example.com",
        subject="Test",
        html="<p>Hi</p>",
        from_address="custom@example.com",
    )
    await provider.send_email(msg)
    import json

    body = json.loads(route.calls[0].request.content)
    assert body["from"] == "custom@example.com"


@respx.mock
async def test_send_email_retries_on_429(provider: ResendEmailProvider, message: EmailMessage) -> None:
    route = respx.post("https://api.resend.com/emails").mock(
        side_effect=[
            httpx.Response(429, text="Rate limited"),
            httpx.Response(200, json={"id": "msg_123"}),
        ]
    )
    await provider.send_email(message)
    assert route.call_count == 2


@respx.mock
async def test_send_email_retries_on_500(provider: ResendEmailProvider, message: EmailMessage) -> None:
    route = respx.post("https://api.resend.com/emails").mock(
        side_effect=[
            httpx.Response(500, text="Server error"),
            httpx.Response(200, json={"id": "msg_123"}),
        ]
    )
    await provider.send_email(message)
    assert route.call_count == 2


@respx.mock
async def test_send_email_raises_after_max_retries(provider: ResendEmailProvider, message: EmailMessage) -> None:
    respx.post("https://api.resend.com/emails").mock(return_value=httpx.Response(500, text="Server error"))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.send_email(message)


@respx.mock
async def test_send_email_raises_on_4xx(provider: ResendEmailProvider, message: EmailMessage) -> None:
    respx.post("https://api.resend.com/emails").mock(return_value=httpx.Response(422, text="Validation error"))
    with pytest.raises(httpx.HTTPStatusError):
        await provider.send_email(message)


async def test_is_available_true(provider: ResendEmailProvider) -> None:
    assert await provider.is_available() is True


async def test_is_available_false_no_key() -> None:
    p = ResendEmailProvider(api_key="", default_from="noreply@example.com")
    assert await p.is_available() is False


async def test_is_available_false_no_from() -> None:
    p = ResendEmailProvider(api_key="re_test", default_from="")
    assert await p.is_available() is False


def test_provider_id(provider: ResendEmailProvider) -> None:
    assert provider.provider_id == "resend"
