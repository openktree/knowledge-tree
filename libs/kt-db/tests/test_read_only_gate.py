"""Tests for the read-only gate helper."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kt_config.errors import GraphReadOnlyError
from kt_db.read_only import assert_writable


def _fake_graph(*, read_only: bool, reason: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id="graph-abc", read_only=read_only, read_only_reason=reason)


def test_writable_graph_does_not_raise() -> None:
    assert_writable(_fake_graph(read_only=False))


def test_owner_locked_raises_with_reason() -> None:
    with pytest.raises(GraphReadOnlyError) as exc:
        assert_writable(_fake_graph(read_only=True, reason="owner"))
    assert exc.value.reason == "owner"
    assert exc.value.graph_id == "graph-abc"


def test_migrating_raises_with_reason() -> None:
    with pytest.raises(GraphReadOnlyError) as exc:
        assert_writable(_fake_graph(read_only=True, reason="migrating"))
    assert exc.value.reason == "migrating"


def test_missing_reason_defaults_to_unknown() -> None:
    with pytest.raises(GraphReadOnlyError) as exc:
        assert_writable(_fake_graph(read_only=True, reason=None))
    assert exc.value.reason == "unknown"
