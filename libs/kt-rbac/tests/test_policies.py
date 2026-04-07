"""Tests for role-to-permission mappings."""

from kt_rbac.policies import (
    DEFAULT_GRAPH_PUBLIC_PERMISSIONS,
    GRAPH_ROLE_PERMISSIONS,
    SUPERADMIN_PERMISSIONS,
)
from kt_rbac.types import GraphRole, Permission


def test_role_hierarchy_reader_subset_of_writer() -> None:
    reader_perms = GRAPH_ROLE_PERMISSIONS[GraphRole.reader]
    writer_perms = GRAPH_ROLE_PERMISSIONS[GraphRole.writer]
    assert reader_perms < writer_perms, "Reader permissions should be a strict subset of writer"


def test_role_hierarchy_writer_subset_of_admin() -> None:
    writer_perms = GRAPH_ROLE_PERMISSIONS[GraphRole.writer]
    admin_perms = GRAPH_ROLE_PERMISSIONS[GraphRole.admin]
    assert writer_perms < admin_perms, "Writer permissions should be a strict subset of admin"


def test_superadmin_has_all_permissions() -> None:
    for p in Permission:
        assert p in SUPERADMIN_PERMISSIONS, f"Superadmin missing {p.name}"


def test_reader_cannot_write() -> None:
    reader_perms = GRAPH_ROLE_PERMISSIONS[GraphRole.reader]
    assert Permission.GRAPH_WRITE not in reader_perms
    assert Permission.SOURCE_WRITE not in reader_perms


def test_reader_can_read() -> None:
    reader_perms = GRAPH_ROLE_PERMISSIONS[GraphRole.reader]
    assert Permission.GRAPH_READ in reader_perms
    assert Permission.SOURCE_READ in reader_perms


def test_writer_can_write() -> None:
    writer_perms = GRAPH_ROLE_PERMISSIONS[GraphRole.writer]
    assert Permission.GRAPH_WRITE in writer_perms
    assert Permission.SOURCE_WRITE in writer_perms


def test_writer_cannot_manage_members() -> None:
    writer_perms = GRAPH_ROLE_PERMISSIONS[GraphRole.writer]
    assert Permission.GRAPH_MANAGE_MEMBERS not in writer_perms


def test_admin_can_manage_members() -> None:
    admin_perms = GRAPH_ROLE_PERMISSIONS[GraphRole.admin]
    assert Permission.GRAPH_MANAGE_MEMBERS in admin_perms
    assert Permission.GRAPH_MANAGE_METADATA in admin_perms


def test_default_graph_public_permissions() -> None:
    assert Permission.GRAPH_READ in DEFAULT_GRAPH_PUBLIC_PERMISSIONS
    assert Permission.SOURCE_READ in DEFAULT_GRAPH_PUBLIC_PERMISSIONS
    assert Permission.GRAPH_WRITE not in DEFAULT_GRAPH_PUBLIC_PERMISSIONS


def test_no_graph_role_has_system_permissions() -> None:
    """System permissions are never granted through graph roles."""
    system_perms = {p for p in Permission if p.value.startswith("system:")}
    for role, perms in GRAPH_ROLE_PERMISSIONS.items():
        overlap = perms & system_perms
        assert not overlap, f"{role.name} has system permissions: {overlap}"
