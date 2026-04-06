"""Tests for source-level access evaluation."""

import uuid

from kt_rbac.checker import PermissionChecker
from kt_rbac.context import PermissionContext
from kt_rbac.source_access import can_access_fact, can_access_source
from kt_rbac.types import GraphRole

_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _ctx(
    *,
    is_superuser: bool = False,
    graph_role: GraphRole | None = GraphRole.reader,
    user_groups: frozenset[str] | None = None,
) -> PermissionContext:
    return PermissionContext(
        user_id=_USER_ID,
        is_superuser=is_superuser,
        graph_role=graph_role,
        user_groups=user_groups or frozenset(),
    )


class TestCanAccessSource:
    def test_none_access_groups_is_public(self) -> None:
        assert can_access_source(_ctx(), None) is True

    def test_empty_access_groups_is_public(self) -> None:
        assert can_access_source(_ctx(), []) is True

    def test_user_in_matching_group(self) -> None:
        ctx = _ctx(user_groups=frozenset({"finance", "engineering"}))
        assert can_access_source(ctx, ["finance"]) is True

    def test_user_not_in_any_group(self) -> None:
        ctx = _ctx(user_groups=frozenset({"engineering"}))
        assert can_access_source(ctx, ["finance", "legal"]) is False

    def test_user_with_no_groups_denied_restricted_source(self) -> None:
        ctx = _ctx(user_groups=frozenset())
        assert can_access_source(ctx, ["finance"]) is False

    def test_superuser_bypasses_access_groups(self) -> None:
        ctx = _ctx(is_superuser=True, user_groups=frozenset())
        assert can_access_source(ctx, ["top_secret"]) is True

    def test_graph_admin_bypasses_access_groups(self) -> None:
        ctx = _ctx(graph_role=GraphRole.admin, user_groups=frozenset())
        assert can_access_source(ctx, ["top_secret"]) is True

    def test_graph_writer_respects_access_groups(self) -> None:
        ctx = _ctx(graph_role=GraphRole.writer, user_groups=frozenset())
        assert can_access_source(ctx, ["finance"]) is False


class TestCanAccessFact:
    def test_fact_with_no_sources_is_public(self) -> None:
        assert can_access_fact(_ctx(), []) is True

    def test_fact_with_one_public_source(self) -> None:
        assert can_access_fact(_ctx(), [None]) is True

    def test_fact_with_one_restricted_source_user_has_group(self) -> None:
        ctx = _ctx(user_groups=frozenset({"finance"}))
        assert can_access_fact(ctx, [["finance"]]) is True

    def test_fact_with_one_restricted_source_user_lacks_group(self) -> None:
        ctx = _ctx(user_groups=frozenset({"engineering"}))
        assert can_access_fact(ctx, [["finance"]]) is False

    def test_fact_with_mixed_sources_one_public(self) -> None:
        """If any source is public, the fact is visible."""
        ctx = _ctx(user_groups=frozenset())
        assert can_access_fact(ctx, [["finance"], None]) is True

    def test_fact_with_mixed_sources_one_matching(self) -> None:
        """If user has access to any source, the fact is visible."""
        ctx = _ctx(user_groups=frozenset({"finance"}))
        assert can_access_fact(ctx, [["finance"], ["legal"]]) is True

    def test_fact_with_all_restricted_sources_no_match(self) -> None:
        ctx = _ctx(user_groups=frozenset({"engineering"}))
        assert can_access_fact(ctx, [["finance"], ["legal"]]) is False

    def test_superuser_sees_all_facts(self) -> None:
        ctx = _ctx(is_superuser=True)
        assert can_access_fact(ctx, [["top_secret"]]) is True


class TestCheckerSourceMethods:
    """Test that PermissionChecker delegates correctly to source_access module."""

    def test_check_source_access(self) -> None:
        checker = PermissionChecker()
        ctx = _ctx(user_groups=frozenset({"finance"}))
        assert checker.check_source_access(ctx, ["finance"]) is True
        assert checker.check_source_access(ctx, ["legal"]) is False

    def test_check_fact_access(self) -> None:
        checker = PermissionChecker()
        ctx = _ctx(user_groups=frozenset({"finance"}))
        assert checker.check_fact_access(ctx, [["finance"], ["legal"]]) is True
        assert checker.check_fact_access(ctx, [["legal"]]) is False
