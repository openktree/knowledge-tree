"""Unit tests for graph API schemas and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kt_api.graphs import (
    _SCHEMA_NAME_RE,
    _SLUG_RE,
    AddMemberRequest,
    CreateGraphRequest,
    GraphResponse,
    UpdateMemberRoleRequest,
)


class TestSlugValidation:
    def test_valid_slugs(self):
        for slug in ["my-graph", "test123", "a1b", "graph_test_data"]:
            assert _SLUG_RE.match(slug), f"Should match: {slug}"

    def test_invalid_slugs(self):
        for slug in ["", "ab", "-start", "end-", "UPPER", "has space", "a" * 200]:
            assert not _SLUG_RE.match(slug), f"Should not match: {slug}"

    def test_default_reserved(self):
        assert _SLUG_RE.match("default")  # regex allows it, but endpoint rejects it


class TestSchemaNameValidation:
    def test_valid_schema_names(self):
        for name in ["graph_test", "graph_my_research", "abc123"]:
            assert _SCHEMA_NAME_RE.match(name), f"Should match: {name}"

    def test_invalid_schema_names(self):
        for name in ["graph-test", "graph test", 'graph"test', "GRAPH_TEST", ""]:
            assert not _SCHEMA_NAME_RE.match(name), f"Should not match: {name}"


class TestCreateGraphRequest:
    def test_defaults(self):
        req = CreateGraphRequest(slug="test-graph", name="Test Graph")
        assert req.storage_mode == "schema"
        assert req.graph_type == "v1"
        assert req.byok_enabled is False
        assert req.database_connection_config_key is None

    def test_custom_values(self):
        req = CreateGraphRequest(
            slug="test-graph",
            name="Test Graph",
            description="A test graph",
            graph_type="v1",
            byok_enabled=True,
            storage_mode="database",
            database_connection_config_key="my-db",
        )
        assert req.byok_enabled is True
        assert req.storage_mode == "database"

    def test_invalid_storage_mode(self):
        with pytest.raises(ValidationError):
            CreateGraphRequest(slug="test", name="Test", storage_mode="invalid")

    def test_slug_too_short(self):
        with pytest.raises(ValidationError):
            CreateGraphRequest(slug="ab", name="Test")


class TestGraphResponse:
    def test_full_response(self):
        resp = GraphResponse(
            id="abc",
            slug="test",
            name="Test",
            is_default=False,
            graph_type="v1",
            byok_enabled=True,
            storage_mode="schema",
            schema_name="graph_test",
            status="active",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            member_count=3,
            node_count=42,
        )
        assert resp.byok_enabled is True
        assert resp.node_count == 42


class TestAddMemberRequest:
    def test_defaults(self):
        req = AddMemberRequest(user_id="abc")
        assert req.role == "reader"

    def test_invalid_role(self):
        with pytest.raises(ValidationError):
            AddMemberRequest(user_id="abc", role="superadmin")

    def test_valid_roles(self):
        for role in ["reader", "writer", "admin"]:
            req = AddMemberRequest(user_id="abc", role=role)
            assert req.role == role


class TestUpdateMemberRoleRequest:
    def test_valid(self):
        req = UpdateMemberRoleRequest(role="writer")
        assert req.role == "writer"

    def test_invalid(self):
        with pytest.raises(ValidationError):
            UpdateMemberRoleRequest(role="owner")
