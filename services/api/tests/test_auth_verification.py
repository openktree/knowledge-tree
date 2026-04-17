"""Tests for email-verification-required login gate + resend flow."""

from __future__ import annotations

import pytest

from kt_config.errors import ConfigurationError
from kt_config.settings import Settings


class TestSettings:
    def test_default_false(self) -> None:
        s = Settings()
        assert s.email_verification_required is False

    def test_explicit_true(self) -> None:
        s = Settings(email_verification_required=True)
        assert s.email_verification_required is True


class TestUserManagerTokenLifetime:
    def test_verification_token_lifetime_is_24h(self) -> None:
        from kt_api.auth.manager import UserManager

        assert UserManager.verification_token_lifetime_seconds == 86400


class TestStartupValidation:
    def _settings(self, **overrides) -> Settings:  # type: ignore[no-untyped-def]
        defaults = dict(
            email_enabled=False,
            email_verification=False,
            email_verification_required=False,
        )
        defaults.update(overrides)
        return Settings(**defaults)

    def test_noop_when_required_false(self) -> None:
        from kt_api.main import _validate_email_verification_config

        _validate_email_verification_config(self._settings())  # no raise

    def test_passes_when_all_flags_aligned(self) -> None:
        from kt_api.main import _validate_email_verification_config

        _validate_email_verification_config(
            self._settings(
                email_enabled=True,
                email_verification=True,
                email_verification_required=True,
            )
        )  # no raise

    def test_raises_when_required_but_email_disabled(self) -> None:
        from kt_api.main import _validate_email_verification_config

        with pytest.raises(ConfigurationError, match="email_verification_required"):
            _validate_email_verification_config(
                self._settings(
                    email_enabled=False,
                    email_verification=True,
                    email_verification_required=True,
                )
            )

    def test_raises_when_required_but_verification_disabled(self) -> None:
        from kt_api.main import _validate_email_verification_config

        with pytest.raises(ConfigurationError, match="email_verification_required"):
            _validate_email_verification_config(
                self._settings(
                    email_enabled=True,
                    email_verification=False,
                    email_verification_required=True,
                )
            )


class TestAuthFeaturesResponse:
    def test_shape_includes_required_flag(self) -> None:
        """Schema contract: response includes email_verification_required."""
        from kt_api.auth.router import AuthFeaturesResponse

        resp = AuthFeaturesResponse(
            google_oauth_enabled=False,
            email_verification_enabled=True,
            email_verification_required=True,
        )
        assert resp.email_verification_required is True
