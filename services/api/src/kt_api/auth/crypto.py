"""Fernet encryption for BYOK API keys."""

from __future__ import annotations

from cryptography.fernet import Fernet

from kt_config.settings import get_settings


def _get_fernet() -> Fernet:
    key = get_settings().byok_encryption_key
    if not key:
        raise ValueError("BYOK_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_api_key(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
