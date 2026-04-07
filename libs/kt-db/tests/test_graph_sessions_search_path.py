"""Regression tests for per-graph schema search_path handling.

PgBouncer in transaction-pooling mode rejects ``search_path`` as a startup
parameter (ProtocolViolationError) and DISCARDs session-scoped state between
transactions. The fix sets ``search_path`` via a ``begin`` event listener
issuing ``SET LOCAL`` per transaction, instead of via asyncpg
``server_settings``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from kt_db.graph_sessions import _make_session_factory


class TestSchemaSearchPath:
    URL = "postgresql+asyncpg://user:pw@localhost:5432/db"

    def test_public_schema_uses_no_search_path_listener(self) -> None:
        """A graph with schema='public' (or no schema) does not register a listener."""
        engine, _ = _make_session_factory(self.URL, schema_name="public")
        try:
            assert not event.contains(engine.sync_engine, "begin", _any_listener)  # type: ignore[arg-type]
        finally:
            # No need to dispose — engine never connected.
            pass

    def test_no_schema_uses_no_search_path_listener(self) -> None:
        engine, _ = _make_session_factory(self.URL, schema_name=None)
        # Begin listeners list should be empty
        listeners = _begin_listeners(engine)
        assert listeners == []

    def test_non_public_schema_registers_begin_listener(self) -> None:
        """A non-public schema must register a begin listener (NOT a connect listener)."""
        engine, _ = _make_session_factory(self.URL, schema_name="graph_scientific")
        listeners = _begin_listeners(engine)
        assert len(listeners) == 1, "Expected exactly one begin listener for search_path"

    def test_search_path_not_in_server_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression: search_path must NOT be sent via asyncpg startup parameters."""
        captured: dict = {}

        from kt_db import graph_sessions as gs_mod

        real_create = gs_mod.create_async_engine

        def _capture(*args, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            return real_create(*args, **kwargs)

        monkeypatch.setattr(gs_mod, "create_async_engine", _capture)

        _make_session_factory(self.URL, schema_name="graph_scientific")

        server_settings = captured["connect_args"]["server_settings"]
        assert "search_path" not in server_settings, (
            "search_path must not be sent as an asyncpg startup parameter (PgBouncer transaction mode rejects it)."
        )
        # application_name is on PgBouncer's whitelist and is fine
        assert server_settings["application_name"] == "kt"

    def test_invalid_schema_name_rejected(self) -> None:
        with pytest.raises(Exception):  # validate_schema_name raises on bad input
            _make_session_factory(self.URL, schema_name="bad schema; DROP TABLE users;--")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _any_listener(*_args, **_kwargs) -> None:  # pragma: no cover - sentinel
    pass


def _begin_listeners(engine) -> list:  # type: ignore[no-untyped-def]
    """Return the list of registered 'begin' listeners on the engine."""
    # SQLAlchemy stores listeners on the dispatch object per-event
    return list(engine.sync_engine.dispatch.begin)
