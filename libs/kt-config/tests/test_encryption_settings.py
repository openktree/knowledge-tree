"""Tests for encryption and TLS settings."""


def test_db_sslmode_default():
    from kt_config.settings import get_settings

    settings = get_settings()
    assert settings.db_sslmode == ""


def test_redis_tls_default():
    from kt_config.settings import get_settings

    settings = get_settings()
    assert settings.redis_tls is False


def test_qdrant_tls_default():
    from kt_config.settings import get_settings

    settings = get_settings()
    assert settings.qdrant_tls is False


def test_encryption_key_default():
    from kt_config.settings import get_settings

    settings = get_settings()
    assert settings.encryption_key == ""


def test_write_db_sslmode_default():
    from kt_config.settings import get_settings

    settings = get_settings()
    assert settings.write_db_sslmode == ""
