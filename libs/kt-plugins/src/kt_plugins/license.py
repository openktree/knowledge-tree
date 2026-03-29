"""License key validation for commercial plugins."""

from __future__ import annotations

import hashlib
import hmac
import logging

from kt_plugins.errors import PluginLicenseError

logger = logging.getLogger(__name__)

# Public key used for offline HMAC validation.
# In production, each commercial plugin ships with its own public key.
# This is a placeholder for the signing infrastructure.
_DEFAULT_SIGNING_KEY = b"kt-plugin-license-v1"


def validate_license_key(
    plugin_id: str,
    license_key: str,
    *,
    signing_key: bytes = _DEFAULT_SIGNING_KEY,
) -> bool:
    """Validate a license key for a commercial plugin.

    The license key format is: ``<payload>.<signature>``
    where signature = HMAC-SHA256(signing_key, payload).hex()

    This is a simple offline check. Future versions may support
    online validation, seat counting, and expiry dates.

    Args:
        plugin_id: The plugin requesting validation.
        license_key: The key string from configuration.
        signing_key: HMAC signing key (plugin-specific in production).

    Returns:
        True if valid.

    Raises:
        PluginLicenseError: If the key is missing, malformed, or invalid.
    """
    if not license_key:
        raise PluginLicenseError(plugin_id, "No license key configured")

    parts = license_key.rsplit(".", 1)
    if len(parts) != 2:
        raise PluginLicenseError(plugin_id, "Malformed license key")

    payload, signature = parts

    expected = hmac.new(signing_key, payload.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise PluginLicenseError(plugin_id, "Invalid license key")

    logger.info("License validated for plugin %r", plugin_id)
    return True


def generate_license_key(
    payload: str,
    *,
    signing_key: bytes = _DEFAULT_SIGNING_KEY,
) -> str:
    """Generate a license key (for testing and key distribution).

    Args:
        payload: Arbitrary payload string (e.g. org name, expiry, etc.).
        signing_key: HMAC signing key.

    Returns:
        License key in ``<payload>.<signature>`` format.
    """
    signature = hmac.new(signing_key, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"
