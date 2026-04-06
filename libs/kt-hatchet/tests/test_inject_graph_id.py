"""Tests for inject_graph_id and _ensure_dict helpers."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from kt_hatchet.client import (
    _ensure_dict,  # noqa: PLC2701
    inject_graph_id,
)

# -- _ensure_dict -----------------------------------------------------------


class _FakeModel(BaseModel):
    topic: str
    budget: int = 10


def test_ensure_dict_passthrough() -> None:
    d = {"a": 1}
    assert _ensure_dict(d) is d


def test_ensure_dict_pydantic_model() -> None:
    m = _FakeModel(topic="test", budget=5)
    result = _ensure_dict(m)
    assert result == {"topic": "test", "budget": 5}
    assert isinstance(result, dict)


def test_ensure_dict_rejects_non_dict() -> None:
    with pytest.raises(TypeError, match="Expected dict or Pydantic model"):
        _ensure_dict("not a dict")  # type: ignore[arg-type]


# -- inject_graph_id --------------------------------------------------------


def test_inject_graph_id_none_passthrough() -> None:
    d = {"topic": "ai", "budget": 5}
    result = inject_graph_id(d, None)
    assert result is d
    assert "graph_id" not in result


def test_inject_graph_id_injects() -> None:
    d = {"topic": "ai"}
    result = inject_graph_id(d, "abc-123")
    assert result == {"topic": "ai", "graph_id": "abc-123"}
    # Original dict unchanged
    assert "graph_id" not in d


def test_inject_graph_id_with_pydantic_model() -> None:
    m = _FakeModel(topic="test")
    result = inject_graph_id(m, "graph-uuid")
    assert result == {"topic": "test", "budget": 10, "graph_id": "graph-uuid"}
    assert isinstance(result, dict)


def test_inject_graph_id_pydantic_model_none() -> None:
    m = _FakeModel(topic="test")
    result = inject_graph_id(m, None)
    assert result == {"topic": "test", "budget": 10}
    assert "graph_id" not in result
