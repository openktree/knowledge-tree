"""HMAC-SHA256 offline license-key validation for commercial plugins.

Each commercial plugin ships with its own signing key (the "seller"
knows the secret; license keys are issued by signing a payload). No
network — validation runs at startup only.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from kt_plugins.errors import PluginLicenseError

logger = logging.getLogger(__name__)

_DEFAULT_SIGNING_KEY = b"kt-plugin-license-v1"


def generate_license_key(payload: str, *, signing_key: bytes = _DEFAULT_SIGNING_KEY) -> str:
    """Produce a ``<payload>.<signature>`` license key. Used by tests and
    by commercial-plugin issuance tooling (not shipped in the platform).
    """
    signature = hmac.new(signing_key, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def validate_license_key(
    plugin_id: str,
    license_key: str,
    *,
    signing_key: bytes = _DEFAULT_SIGNING_KEY,
) -> bool:
    """Return True for a valid key; raise ``PluginLicenseError`` otherwise."""
    if not license_key:
        raise PluginLicenseError(plugin_id, "no license key configured")

    parts = license_key.rsplit(".", 1)
    if len(parts) != 2:
        raise PluginLicenseError(plugin_id, "malformed license key")

    payload, signature = parts
    expected = hmac.new(signing_key, payload.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise PluginLicenseError(plugin_id, "invalid license key")

    logger.info("license validated for plugin %r", plugin_id)
    return True
