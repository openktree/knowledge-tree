"""Factory for creating email providers based on settings."""

from kt_config.settings import Settings
from kt_providers.email_base import EmailProvider


def create_email_provider(settings: Settings) -> EmailProvider | None:
    """Create an email provider based on settings, or None if disabled."""
    if not settings.email_enabled:
        return None
    if settings.email_provider == "resend" and settings.resend_api_key:
        from kt_providers.email_resend import ResendEmailProvider

        return ResendEmailProvider(
            api_key=settings.resend_api_key,
            default_from=settings.email_from_address,
        )
    return None
