"""Tests for EncryptedString TypeDecorator."""

from cryptography.fernet import Fernet

from kt_db.encrypted_type import EncryptedString, reset_fernet_cache


def _make_type() -> EncryptedString:
    return EncryptedString()


class TestEncryptedStringNoKey:
    """When no encryption_key is set, values pass through as plaintext."""

    def teardown_method(self) -> None:
        reset_fernet_cache()

    def test_bind_param_passthrough(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_KEY", "")
        reset_fernet_cache()
        t = _make_type()
        assert t.process_bind_param("hello", None) == "hello"

    def test_result_value_passthrough(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_KEY", "")
        reset_fernet_cache()
        t = _make_type()
        assert t.process_result_value("hello", None) == "hello"

    def test_none_passthrough(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_KEY", "")
        reset_fernet_cache()
        t = _make_type()
        assert t.process_bind_param(None, None) is None
        assert t.process_result_value(None, None) is None


class TestEncryptedStringWithKey:
    """When encryption_key is set, values are Fernet-encrypted."""

    def teardown_method(self) -> None:
        reset_fernet_cache()

    def test_round_trip(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        reset_fernet_cache()

        t = _make_type()
        encrypted = t.process_bind_param("secret-value", None)

        assert encrypted is not None
        assert encrypted != "secret-value"  # should be ciphertext
        assert encrypted.startswith("gAAAAA")  # Fernet token prefix

        decrypted = t.process_result_value(encrypted, None)
        assert decrypted == "secret-value"

    def test_none_values(self, monkeypatch):
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        reset_fernet_cache()

        t = _make_type()
        assert t.process_bind_param(None, None) is None
        assert t.process_result_value(None, None) is None

    def test_graceful_decrypt_of_plaintext(self, monkeypatch):
        """Pre-encryption plaintext values should be returned as-is."""
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        reset_fernet_cache()

        t = _make_type()
        result = t.process_result_value("not-encrypted-value", None)
        assert result == "not-encrypted-value"

    def test_late_key_configuration(self, monkeypatch):
        """If key is not set initially, it should be picked up on next call."""
        monkeypatch.setenv("ENCRYPTION_KEY", "")
        reset_fernet_cache()

        t = _make_type()
        # First call — no key, plaintext passthrough
        assert t.process_bind_param("hello", None) == "hello"

        # Now set the key and reset cache
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_KEY", key)
        reset_fernet_cache()

        # Should now encrypt
        encrypted = t.process_bind_param("hello", None)
        assert encrypted is not None
        assert encrypted != "hello"
