"""Fernet-based encrypted string column type for SQLAlchemy.

Transparently encrypts on write and decrypts on read.  When no
``ENCRYPTION_KEY`` is configured the column behaves as plain text so
that local development and tests work without extra setup.
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from kt_config.settings import get_settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None
_initialized = False


def _get_fernet() -> Fernet | None:
    """Return a Fernet instance if ``encryption_key`` is configured, else None.

    Re-checks settings when no key was found previously, so that a worker
    that starts before ENCRYPTION_KEY is set will pick it up on the next call.
    """
    global _fernet, _initialized
    if _fernet is not None:
        return _fernet
    if _initialized:
        # Previously checked and no key was set — re-check settings in case
        # the key has been configured since.
        pass
    key = get_settings().encryption_key
    if key:
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    _initialized = True
    return _fernet


def reset_fernet_cache() -> None:
    """Reset cached Fernet instance (for testing)."""
    global _fernet, _initialized
    _fernet = None
    _initialized = False


class EncryptedString(TypeDecorator):
    """A string column that is Fernet-encrypted at rest.

    * Stores ciphertext as ``Text`` in the database.
    * When ``ENCRYPTION_KEY`` is not set, stores and returns plaintext
      (dev/test convenience).
    * On read, attempts decryption first; if it fails (e.g. data written
      before encryption was enabled), returns the raw value.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: object) -> str | None:  # noqa: ARG002
        if value is None:
            return None
        f = _get_fernet()
        if f is None:
            return value
        return f.encrypt(value.encode()).decode()

    def process_result_value(self, value: str | None, dialect: object) -> str | None:  # noqa: ARG002
        if value is None:
            return None
        f = _get_fernet()
        if f is None:
            return value
        try:
            return f.decrypt(value.encode()).decode()
        except InvalidToken:
            # Value was stored before encryption was enabled — return as-is
            logger.debug("Could not decrypt column value; returning raw")
            return value
