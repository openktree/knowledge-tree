"""Unit tests for graph API schemas and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kt_api.graphs import (
    _SLUG_RE,
    AddMemberRequest,
    CreateGraphRequest,
    GraphResponse,
    GraphTypeSummary,
    ReadOnlyToggleRequest,
    UpdateMemberRoleRequest,
)
from kt_db.keys import validate_schema_name


class TestSlugValidation:
    def test_valid_slugs(self):
        for slug in ["my_graph", "test123", "a1b", "graph_test_data"]:
            assert _SLUG_RE.match(slug), f"Should match: {slug}"

    def test_invalid_slugs(self):
        # Hyphens are disallowed to prevent schema name collisions
        for slug in ["", "ab", "-start", "end-", "my-graph", "UPPER", "has space", "a" * 200]:
            assert not _SLUG_RE.match(slug), f"Should not match: {slug}"

    def test_default_reserved(self):
        assert _SLUG_RE.match("default")  # regex allows it, but endpoint rejects it


class TestSchemaNameValidation:
    def test_valid_schema_names(self):
        for name in ["graph_test", "graph_my_research", "abc123"]:
            validate_schema_name(name)  # should not raise

    def test_invalid_schema_names(self):
        for name in ["graph-test", "graph test", 'graph"test', "GRAPH_TEST", ""]:
            with pytest.raises(ValueError):
                validate_schema_name(name)


class TestCreateGraphRequest:
    def test_defaults(self):
        req = CreateGraphRequest(slug="test_graph", name="Test Graph")
        assert req.graph_type == "v1"
        assert req.graph_type_id == "default"
        assert req.byok_enabled is False
        assert req.database_connection_config_key is None

    def test_graph_type_id_custom(self):
        req = CreateGraphRequest(slug="my_graph", name="X", graph_type_id="science")
        assert req.graph_type_id == "science"

    def test_graph_type_id_invalid_chars(self):
        with pytest.raises(ValidationError):
            CreateGraphRequest(slug="my_graph", name="X", graph_type_id="Bad Type!")

    def test_custom_values(self):
        req = CreateGraphRequest(
            slug="test_graph",
            name="Test Graph",
            description="A test graph",
            graph_type="v1",
            byok_enabled=True,
            database_connection_config_key="my-db",
        )
        assert req.byok_enabled is True
        assert req.database_connection_config_key == "my-db"

    def test_default_connection_key_accepted(self):
        # "default" is the magic string the UI sends for the system DB
        req = CreateGraphRequest(slug="test_graph", name="Test", database_connection_config_key="default")
        assert req.database_connection_config_key == "default"

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
            graph_type_id="default",
            graph_type_version=1,
            graph_type_info=GraphTypeSummary(id="default", display_name="Default", current_version=1),
            read_only=False,
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
        assert resp.graph_type_id == "default"
        assert resp.graph_type_version == 1
        assert resp.graph_type_info.current_version == 1
        assert resp.read_only is False

    def test_read_only_reason(self):
        resp = GraphResponse(
            id="abc",
            slug="test",
            name="Test",
            is_default=False,
            graph_type="v1",
            graph_type_id="default",
            graph_type_version=1,
            read_only=True,
            read_only_reason="migrating",
            byok_enabled=False,
            storage_mode="schema",
            schema_name="graph_test",
            status="active",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        assert resp.read_only is True
        assert resp.read_only_reason == "migrating"


class TestReadOnlyToggleRequest:
    def test_enable(self):
        req = ReadOnlyToggleRequest(read_only=True)
        assert req.read_only is True

    def test_disable(self):
        req = ReadOnlyToggleRequest(read_only=False)
        assert req.read_only is False

    def test_missing_field(self):
        with pytest.raises(ValidationError):
            ReadOnlyToggleRequest()  # type: ignore[call-arg]


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
