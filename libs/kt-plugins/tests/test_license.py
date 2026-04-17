"""License-key HMAC validation."""

from __future__ import annotations

import pytest

from kt_plugins import PluginLicenseError, generate_license_key, validate_license_key


def test_round_trip_default_signing_key() -> None:
    key = generate_license_key("acme-corp:expires=2099-01-01")
    assert validate_license_key("billing", key) is True


def test_validates_with_custom_signing_key() -> None:
    signing = b"custom-per-plugin-secret"
    key = generate_license_key("payload", signing_key=signing)
    assert validate_license_key("plugin-x", key, signing_key=signing) is True


def test_rejects_missing_key() -> None:
    with pytest.raises(PluginLicenseError, match="no license key"):
        validate_license_key("plugin-x", "")


def test_rejects_malformed_key() -> None:
    with pytest.raises(PluginLicenseError, match="malformed"):
        validate_license_key("plugin-x", "no-signature-segment")


def test_rejects_tampered_payload() -> None:
    key = generate_license_key("legit-payload")
    # Flip first char of payload — signature no longer matches.
    tampered = "x" + key[1:]
    with pytest.raises(PluginLicenseError, match="invalid"):
        validate_license_key("plugin-x", tampered)


def test_rejects_wrong_signing_key() -> None:
    key = generate_license_key("payload", signing_key=b"seller-a")
    with pytest.raises(PluginLicenseError, match="invalid"):
        validate_license_key("plugin-x", key, signing_key=b"seller-b")
