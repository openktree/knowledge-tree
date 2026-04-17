"""Unit tests for ``_provision_graph`` URL routing.

The function previously hardcoded the system DB; with multi-graph support
it must route schema creation, alembic migrations, and Qdrant collection
creation to the database referenced by ``graph.database_connection_id``.

These tests stub out ``create_async_engine``, the in-process alembic
runner (``ensure_graph_schema_migrated``), and the Qdrant client
factories so we can assert the routing logic without actually hitting
any database.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_config.settings import GraphDatabaseConfig
from kt_db.models import DatabaseConnection, Graph


def _make_graph(*, database_connection_id: uuid.UUID | None) -> Graph:
    g = Graph()
    g.id = uuid.uuid4()
    g.slug = "prov_test"
    g.schema_name = "graph_prov_test"
    g.database_connection_id = database_connection_id
    g.is_default = False
    g.storage_mode = "schema"
    g.status = "provisioning"
    return g


def _fake_async_engine_ctx() -> MagicMock:
    """Build a MagicMock that mimics ``create_async_engine().begin().__aenter__/__aexit__``."""
    eng = MagicMock(name="async_engine")
    conn = AsyncMock(name="async_conn")

    @asynccontextmanager
    async def _begin():
        yield conn

    eng.begin = _begin
    eng.dispose = AsyncMock()
    return eng


@pytest.fixture
def stub_provision_settings(monkeypatch):
    """Override get_settings() so _provision_graph sees a known graph_databases."""
    from kt_config.settings import Settings

    cfg = GraphDatabaseConfig(
        graph_database_url="postgresql+asyncpg://kt:pw@shared-graph-rw:5432/knowledge_tree_shared",
        write_database_url="postgresql+asyncpg://kt:pw@shared-pgbouncer:5432/knowledge_tree_shared_write",
        qdrant_url="http://shared-qdrant:6333",
    )

    s = Settings()
    s.graph_databases = {"shared": cfg}
    s.qdrant_url = "http://localhost:6333"

    # _provision_graph imports get_settings from kt_config.settings; patch the
    # in-module reference (graphs.py imports it at module top in this PR).
    monkeypatch.setattr("kt_api.graphs.get_settings", lambda: s)
    return s


@pytest.mark.asyncio
async def test_provision_routes_to_external_db_when_connection_set(stub_provision_settings):
    """When ``database_connection_id`` is set, schemas + alembic + Qdrant must
    target the external DB referenced by ``Settings.graph_databases``."""
    from kt_api import graphs as graphs_mod

    db_conn = DatabaseConnection(id=uuid.uuid4(), name="Shared", config_key="shared")
    graph = _make_graph(database_connection_id=db_conn.id)

    repo_mock = MagicMock(name="GraphRepository")
    repo_mock.get_database_connection = AsyncMock(return_value=db_conn)

    captured_urls: list[str] = []

    def fake_create_async_engine(url, **_kwargs):
        captured_urls.append(url)
        return _fake_async_engine_ctx()

    fake_qdrant_client = MagicMock(name="qdrant_client")
    fake_qdrant_client.close = AsyncMock()

    captured_qdrant_urls: list[str] = []

    def fake_make_qdrant(url, timeout=None):
        captured_qdrant_urls.append(url)
        return fake_qdrant_client

    fake_migrate_calls: list[dict] = []

    async def fake_ensure_graph_schema_migrated(slug, *, graph_db_url, write_db_url):
        fake_migrate_calls.append({"slug": slug, "graph_db_url": graph_db_url, "write_db_url": write_db_url})

    fake_collection_repo = MagicMock()
    fake_collection_repo.ensure_collection = AsyncMock()

    with (
        patch("kt_api.graphs.GraphRepository", return_value=repo_mock),
        patch("sqlalchemy.ext.asyncio.create_async_engine", side_effect=fake_create_async_engine),
        patch("kt_qdrant.client.make_qdrant_client", side_effect=fake_make_qdrant),
        patch("kt_qdrant.client.get_qdrant_client", return_value=MagicMock()),
        patch("kt_qdrant.repositories.facts.QdrantFactRepository", return_value=fake_collection_repo),
        patch("kt_qdrant.repositories.nodes.QdrantNodeRepository", return_value=fake_collection_repo),
        patch("kt_qdrant.repositories.seeds.QdrantSeedRepository", return_value=fake_collection_repo),
        patch(
            "kt_db.startup.ensure_graph_schema_migrated",
            side_effect=fake_ensure_graph_schema_migrated,
        ),
    ):
        session_mock = MagicMock()
        resolver_mock = MagicMock()
        await graphs_mod._provision_graph(graph, session_mock, resolver_mock)

    # Both engines were created against the EXTERNAL DB URLs
    assert "postgresql+asyncpg://kt:pw@shared-graph-rw:5432/knowledge_tree_shared" in captured_urls
    assert "postgresql+asyncpg://kt:pw@shared-pgbouncer:5432/knowledge_tree_shared_write" in captured_urls
    # System DB URLs were NOT touched
    assert not any("localhost" in u for u in captured_urls)

    # Per-graph Qdrant client was constructed against the external Qdrant
    assert captured_qdrant_urls == ["http://shared-qdrant:6333"]
    fake_qdrant_client.close.assert_awaited()  # cleaned up the per-graph client

    # Alembic ran in-process with the external DB URLs for this schema
    assert len(fake_migrate_calls) == 1
    call = fake_migrate_calls[0]
    assert call["slug"] == "graph_prov_test"
    assert call["graph_db_url"] == "postgresql+asyncpg://kt:pw@shared-graph-rw:5432/knowledge_tree_shared"
    assert call["write_db_url"] == "postgresql+asyncpg://kt:pw@shared-pgbouncer:5432/knowledge_tree_shared_write"


@pytest.mark.asyncio
async def test_provision_routes_to_system_db_when_connection_null(stub_provision_settings):
    """When ``database_connection_id`` is NULL, schemas + alembic + Qdrant must
    target the system DBs from Settings."""
    from kt_api import graphs as graphs_mod

    graph = _make_graph(database_connection_id=None)

    captured_urls: list[str] = []

    def fake_create_async_engine(url, **_kwargs):
        captured_urls.append(url)
        return _fake_async_engine_ctx()

    fake_migrate_calls: list[dict] = []

    async def fake_ensure_graph_schema_migrated(slug, *, graph_db_url, write_db_url):
        fake_migrate_calls.append({"slug": slug, "graph_db_url": graph_db_url, "write_db_url": write_db_url})

    fake_collection_repo = MagicMock()
    fake_collection_repo.ensure_collection = AsyncMock()

    fake_singleton = MagicMock()
    fake_singleton.close = AsyncMock()

    with (
        patch("sqlalchemy.ext.asyncio.create_async_engine", side_effect=fake_create_async_engine),
        patch("kt_qdrant.client.get_qdrant_client", return_value=fake_singleton),
        patch("kt_qdrant.client.make_qdrant_client") as make_mock,
        patch("kt_qdrant.repositories.facts.QdrantFactRepository", return_value=fake_collection_repo),
        patch("kt_qdrant.repositories.nodes.QdrantNodeRepository", return_value=fake_collection_repo),
        patch("kt_qdrant.repositories.seeds.QdrantSeedRepository", return_value=fake_collection_repo),
        patch(
            "kt_db.startup.ensure_graph_schema_migrated",
            side_effect=fake_ensure_graph_schema_migrated,
        ),
    ):
        session_mock = MagicMock()
        resolver_mock = MagicMock()
        await graphs_mod._provision_graph(graph, session_mock, resolver_mock)

    # Engines were created against the SYSTEM DB URLs from settings
    s = stub_provision_settings
    assert s.database_url in captured_urls
    assert s.write_database_url in captured_urls

    # The singleton Qdrant client was reused — no per-graph factory call
    make_mock.assert_not_called()
    fake_singleton.close.assert_not_awaited()  # singleton is not closed by us

    # Alembic ran in-process against the SYSTEM DB URLs
    assert len(fake_migrate_calls) == 1
    call = fake_migrate_calls[0]
    assert call["graph_db_url"] == s.database_url
    assert call["write_db_url"] == s.write_database_url


@pytest.mark.asyncio
async def test_provision_raises_when_config_key_missing_from_settings(stub_provision_settings):
    """If a row references a config_key not in Settings.graph_databases, fail loudly."""
    from kt_api import graphs as graphs_mod

    db_conn = DatabaseConnection(id=uuid.uuid4(), name="Missing", config_key="missing_key")
    graph = _make_graph(database_connection_id=db_conn.id)

    repo_mock = MagicMock()
    repo_mock.get_database_connection = AsyncMock(return_value=db_conn)

    with patch("kt_api.graphs.GraphRepository", return_value=repo_mock):
        session_mock = MagicMock()
        resolver_mock = MagicMock()
        with pytest.raises(RuntimeError, match="not configured in settings.graph_databases"):
            await graphs_mod._provision_graph(graph, session_mock, resolver_mock)
