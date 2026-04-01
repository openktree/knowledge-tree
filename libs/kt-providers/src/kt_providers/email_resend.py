"""Resend.com email provider using their HTTP API directly."""

import asyncio

import httpx
import structlog

from kt_providers.email_base import EmailMessage, EmailProvider

logger = structlog.get_logger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds


class ResendEmailProvider(EmailProvider):
    """Resend.com email provider using their HTTP API directly."""

    API_URL = "https://api.resend.com/emails"

    def __init__(self, api_key: str, default_from: str) -> None:
        self._api_key = api_key
        self._default_from = default_from

    @property
    def provider_id(self) -> str:
        return "resend"

    async def send_email(self, message: EmailMessage) -> None:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "from": message.from_address or self._default_from,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
        }

        last_exc: httpx.HTTPStatusError | None = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(_MAX_RETRIES):
                response = await client.post(self.API_URL, headers=headers, json=payload)
                if response.status_code not in _RETRYABLE_STATUS_CODES:
                    response.raise_for_status()
                    return
                last_exc = httpx.HTTPStatusError(
                    f"{response.status_code} {response.reason_phrase}",
                    request=response.request,
                    response=response,
                )
                delay = _BASE_DELAY * (2**attempt)
                logger.warning(
                    "resend_email_retrying",
                    status=response.status_code,
                    attempt=attempt + 1,
                    delay=delay,
                )
                await asyncio.sleep(delay)
            raise last_exc  # type: ignore[misc]

    async def is_available(self) -> bool:
        return bool(self._api_key and self._default_from)
