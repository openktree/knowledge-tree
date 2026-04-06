"""Tests for RBAC type enums."""

from kt_rbac.types import GraphRole, Permission, SystemRole


def test_graph_role_values_match_db_strings() -> None:
    """GraphRole enum values must match the strings stored in GraphMember.role."""
    assert GraphRole.reader.value == "reader"
    assert GraphRole.writer.value == "writer"
    assert GraphRole.admin.value == "admin"


def test_system_role_values() -> None:
    assert SystemRole.user.value == "user"
    assert SystemRole.superadmin.value == "superadmin"


def test_permission_namespacing() -> None:
    """All permissions are namespaced by scope."""
    for p in Permission:
        assert ":" in p.value, f"{p.name} is not namespaced"


def test_system_permissions_start_with_system() -> None:
    system_perms = [p for p in Permission if p.value.startswith("system:")]
    assert len(system_perms) == 5


def test_graph_permissions_start_with_graph() -> None:
    graph_perms = [p for p in Permission if p.value.startswith("graph:")]
    assert len(graph_perms) == 5


def test_source_permissions_start_with_source() -> None:
    source_perms = [p for p in Permission if p.value.startswith("source:")]
    assert len(source_perms) == 2


def test_graph_role_is_str_enum() -> None:
    """GraphRole should be usable as a plain string (for Pydantic/DB compat)."""
    assert isinstance(GraphRole.reader, str)
    assert GraphRole.reader == "reader"
