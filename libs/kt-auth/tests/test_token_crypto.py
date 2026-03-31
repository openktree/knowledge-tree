"""Unit tests for token crypto helpers (no DB required)."""

from kt_auth.tokens import generate_token, hash_token, verify_token


def test_generate_token_has_prefix():
    token = generate_token()
    assert token.startswith("tokn_")
    assert len(token) > 10


def test_hash_and_verify_roundtrip():
    raw = generate_token()
    hashed = hash_token(raw)
    assert verify_token(raw, hashed) is True


def test_verify_wrong_token():
    raw = generate_token()
    hashed = hash_token(raw)
    assert verify_token("tokn_wrong", hashed) is False


def test_verify_malformed_hash():
    assert verify_token("tokn_test", "not_a_bcrypt_hash") is False
