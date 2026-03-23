"""Google OAuth2 client setup."""

from httpx_oauth.clients.google import GoogleOAuth2

from kt_config.settings import get_settings


def get_google_oauth_client() -> GoogleOAuth2:
    settings = get_settings()
    return GoogleOAuth2(settings.google_oauth_client_id, settings.google_oauth_client_secret)
