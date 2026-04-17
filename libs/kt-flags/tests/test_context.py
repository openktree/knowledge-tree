"""EvalContext builder — targeting_key + attributes round-trip."""

from __future__ import annotations

from kt_flags.context import build_eval_context


def test_empty_context() -> None:
    ctx = build_eval_context()
    assert ctx.targeting_key is None
    assert ctx.attributes == {}


def test_user_becomes_targeting_key() -> None:
    ctx = build_eval_context(user_id="u-1")
    assert ctx.targeting_key == "u-1"


def test_attributes_populated() -> None:
    ctx = build_eval_context(
        user_id="u-1",
        tenant_id="t-7",
        graph_id="g-9",
        environment="prod",
        extra={"plan": "pro"},
    )
    assert ctx.attributes == {
        "tenant_id": "t-7",
        "graph_id": "g-9",
        "environment": "prod",
        "plan": "pro",
    }
