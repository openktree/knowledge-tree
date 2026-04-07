"""Tests for PermissionChecker."""

import uuid

import pytest

from kt_rbac.checker import PermissionChecker, PermissionDeniedError
from kt_rbac.context import PermissionContext
from kt_rbac.types import GraphRole, Permission

_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _ctx(
    *,
    is_superuser: bool = False,
    graph_role: GraphRole | None = None,
    is_default_graph: bool = False,
    user_groups: frozenset[str] | None = None,
) -> PermissionContext:
    return PermissionContext(
        user_id=_USER_ID,
        is_superuser=is_superuser,
        graph_role=graph_role,
        is_default_graph=is_default_graph,
        user_groups=user_groups or frozenset(),
    )


@pytest.fixture
def checker() -> PermissionChecker:
    return PermissionChecker()


class TestSuperuser:
    def test_superuser_passes_everything(self, checker: PermissionChecker) -> None:
        ctx = _ctx(is_superuser=True)
        for p in Permission:
            assert checker.check(ctx, p) is True

    def test_superuser_check_or_raise_does_not_raise(self, checker: PermissionChecker) -> None:
        ctx = _ctx(is_superuser=True)
        checker.check_or_raise(ctx, Permission.SYSTEM_ADMIN_OPS)  # should not raise


class TestSystemPermissions:
    def test_non_superuser_blocked_from_system_permissions(self, checker: PermissionChecker) -> None:
        ctx = _ctx(graph_role=GraphRole.admin)
        system_perms = [p for p in Permission if p.value.startswith("system:")]
        for p in system_perms:
            assert checker.check(ctx, p) is False

    def test_check_or_raise_raises_permission_denied(self, checker: PermissionChecker) -> None:
        ctx = _ctx(graph_role=GraphRole.admin)
        with pytest.raises(PermissionDeniedError) as exc_info:
            checker.check_or_raise(ctx, Permission.SYSTEM_ADMIN_OPS)
        assert exc_info.value.permission == Permission.SYSTEM_ADMIN_OPS


class TestGraphPermissions:
    def test_reader_can_read(self, checker: PermissionChecker) -> None:
        ctx = _ctx(graph_role=GraphRole.reader)
        assert checker.check(ctx, Permission.GRAPH_READ) is True
        assert checker.check(ctx, Permission.SOURCE_READ) is True

    def test_reader_cannot_write(self, checker: PermissionChecker) -> None:
        ctx = _ctx(graph_role=GraphRole.reader)
        assert checker.check(ctx, Permission.GRAPH_WRITE) is False
        assert checker.check(ctx, Permission.SOURCE_WRITE) is False

    def test_writer_can_write(self, checker: PermissionChecker) -> None:
        ctx = _ctx(graph_role=GraphRole.writer)
        assert checker.check(ctx, Permission.GRAPH_WRITE) is True

    def test_writer_cannot_manage_members(self, checker: PermissionChecker) -> None:
        ctx = _ctx(graph_role=GraphRole.writer)
        assert checker.check(ctx, Permission.GRAPH_MANAGE_MEMBERS) is False

    def test_admin_can_manage_members(self, checker: PermissionChecker) -> None:
        ctx = _ctx(graph_role=GraphRole.admin)
        assert checker.check(ctx, Permission.GRAPH_MANAGE_MEMBERS) is True
        assert checker.check(ctx, Permission.GRAPH_MANAGE_METADATA) is True

    def test_no_role_means_no_access(self, checker: PermissionChecker) -> None:
        ctx = _ctx(graph_role=None)
        assert checker.check(ctx, Permission.GRAPH_READ) is False


class TestDefaultGraph:
    def test_read_allowed_without_role(self, checker: PermissionChecker) -> None:
        ctx = _ctx(is_default_graph=True)
        assert checker.check(ctx, Permission.GRAPH_READ) is True
        assert checker.check(ctx, Permission.SOURCE_READ) is True

    def test_write_blocked_for_non_superuser(self, checker: PermissionChecker) -> None:
        ctx = _ctx(is_default_graph=True)
        assert checker.check(ctx, Permission.GRAPH_WRITE) is False
        assert checker.check(ctx, Permission.GRAPH_MANAGE_MEMBERS) is False

    def test_write_allowed_for_superuser(self, checker: PermissionChecker) -> None:
        ctx = _ctx(is_superuser=True, is_default_graph=True)
        assert checker.check(ctx, Permission.GRAPH_WRITE) is True
