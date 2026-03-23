import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_postgres_connection(db_session):
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def test_pgvector_extension(db_session):
    result = await db_session.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
    version = result.scalar()
    assert version is not None


async def test_tables_exist(db_session):
    schema_row = await db_session.execute(text("SELECT current_schema"))
    current = schema_row.scalar()
    result = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = :schema"),
        {"schema": current},
    )
    tables = {row[0] for row in result.fetchall()}
    expected_tables = {
        "nodes",
        "edges",
        "dimensions",
        "convergence_reports",
        "divergent_claims",
        "facts",
        "fact_sources",
        "raw_sources",
        "node_facts",
        "node_versions",
        "provider_fetches",
        "ai_models",
        "query_origins",
    }
    assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"


