"""Unit tests for GraphDatabaseConfig validators."""

from __future__ import annotations

from kt_config.settings import GraphDatabaseConfig


class TestGraphDatabaseConfigValidator:
    def test_normalizes_plain_postgresql_url_to_asyncpg(self):
        cfg = GraphDatabaseConfig(
            graph_database_url="postgresql://kt:pw@host:5432/db",
            write_database_url="postgresql://kt:pw@other:5432/db_w",
        )
        assert cfg.graph_database_url == "postgresql+asyncpg://kt:pw@host:5432/db"
        assert cfg.write_database_url == "postgresql+asyncpg://kt:pw@other:5432/db_w"

    def test_keeps_already_async_url(self):
        cfg = GraphDatabaseConfig(
            graph_database_url="postgresql+asyncpg://kt:pw@host:5432/db",
            write_database_url="postgresql+asyncpg://kt:pw@other:5432/db_w",
        )
        assert cfg.graph_database_url == "postgresql+asyncpg://kt:pw@host:5432/db"

    def test_qdrant_url_optional(self):
        cfg = GraphDatabaseConfig(
            graph_database_url="postgresql+asyncpg://kt:pw@h:5432/d",
            write_database_url="postgresql+asyncpg://kt:pw@h:5432/dw",
        )
        assert cfg.qdrant_url == ""
