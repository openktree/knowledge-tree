"""Regression test for the ConfigParser interpolation bug in alembic env.py.

A URL with a URL-encoded password (e.g. ``%2B`` for ``+``) used to crash
``set_main_option`` because Python's ``configparser`` interprets ``%(...)s``
as an interpolation token. The fix escapes ``%`` to ``%%`` before passing
the URL to alembic config.
"""

from __future__ import annotations

from configparser import ConfigParser

import pytest


def _set_unescaped(url: str) -> None:
    """Reproduce the old broken behavior — should raise."""
    cp = ConfigParser()
    cp.add_section("alembic")
    cp.set("alembic", "sqlalchemy.url", url)


def _set_escaped(url: str) -> str:
    """Reproduce the new fix — should round-trip cleanly through configparser."""
    cp = ConfigParser()
    cp.add_section("alembic")
    cp.set("alembic", "sqlalchemy.url", url.replace("%", "%%"))
    return cp.get("alembic", "sqlalchemy.url")


class TestAlembicUrlEscape:
    URL_WITH_ENCODED_PLUS = "postgresql+asyncpg://kt:%2BzYZKIbX2SEPQIhhSSpd2XPsEVv%2BdOwy@host:5432/db"
    URL_PLAIN = "postgresql+asyncpg://kt:plainpw@host:5432/db"

    def test_unescaped_url_with_percent_encoding_raises(self):
        with pytest.raises(ValueError, match="invalid interpolation syntax"):
            _set_unescaped(self.URL_WITH_ENCODED_PLUS)

    def test_escaped_url_round_trips(self):
        # The fix: configparser stores escaped form, .get() unescapes back
        result = _set_escaped(self.URL_WITH_ENCODED_PLUS)
        assert result == self.URL_WITH_ENCODED_PLUS

    def test_escaped_url_plain_password_unchanged(self):
        result = _set_escaped(self.URL_PLAIN)
        assert result == self.URL_PLAIN
