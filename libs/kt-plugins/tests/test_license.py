"""Tests for license key generation and validation."""

from __future__ import annotations

import pytest

from kt_plugins.errors import PluginLicenseError
from kt_plugins.license import generate_license_key, validate_license_key


def test_generate_and_validate() -> None:
    key = generate_license_key("acme-corp-2026")
    assert validate_license_key("billing", key) is True


def test_empty_key_raises() -> None:
    with pytest.raises(PluginLicenseError, match="No license key"):
        validate_license_key("billing", "")


def test_malformed_key_raises() -> None:
    with pytest.raises(PluginLicenseError, match="Malformed"):
        validate_license_key("billing", "no-dot-separator")


def test_invalid_signature_raises() -> None:
    with pytest.raises(PluginLicenseError, match="Invalid"):
        validate_license_key("billing", "payload.invalidsignature")


def test_custom_signing_key() -> None:
    custom_key = b"my-secret-key"
    key = generate_license_key("org-name", signing_key=custom_key)
    assert validate_license_key("sso", key, signing_key=custom_key) is True

    # Should fail with default key
    with pytest.raises(PluginLicenseError):
        validate_license_key("sso", key)
