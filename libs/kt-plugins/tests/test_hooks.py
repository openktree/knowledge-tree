"""Tests for HookRegistry — registration, ordering, trigger, filter."""

from __future__ import annotations

import pytest

from kt_plugins.hooks import HookRegistry


@pytest.fixture
def registry() -> HookRegistry:
    return HookRegistry()


async def test_trigger_no_handlers(registry: HookRegistry) -> None:
    results = await registry.trigger("nonexistent")
    assert results == []


async def test_register_and_trigger(registry: HookRegistry) -> None:
    calls: list[dict] = []

    async def handler(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        return "ok"

    registry.register("test.hook", handler)
    results = await registry.trigger("test.hook", foo="bar")

    assert results == ["ok"]
    assert calls == [{"foo": "bar"}]


async def test_priority_ordering(registry: HookRegistry) -> None:
    order: list[int] = []

    async def handler_a(**_: object) -> None:
        order.append(1)

    async def handler_b(**_: object) -> None:
        order.append(2)

    async def handler_c(**_: object) -> None:
        order.append(3)

    registry.register("test.order", handler_c, priority=300)
    registry.register("test.order", handler_a, priority=10)
    registry.register("test.order", handler_b, priority=50)

    await registry.trigger("test.order")
    assert order == [1, 2, 3]


async def test_filter_chains_value(registry: HookRegistry) -> None:
    async def add_one(value: int, **_: object) -> int:
        return value + 1

    async def double(value: int, **_: object) -> int:
        return value * 2

    registry.register("test.filter", add_one, priority=10)
    registry.register("test.filter", double, priority=20)

    result = await registry.filter("test.filter", 5)
    # 5 -> add_one -> 6 -> double -> 12
    assert result == 12


async def test_filter_empty_returns_original(registry: HookRegistry) -> None:
    result = await registry.filter("nonexistent", "unchanged")
    assert result == "unchanged"


async def test_trigger_error_isolation(registry: HookRegistry) -> None:
    """A failing handler should not prevent others from running."""
    results_log: list[str] = []

    async def good_handler(**_: object) -> str:
        results_log.append("good")
        return "good"

    async def bad_handler(**_: object) -> str:
        raise RuntimeError("boom")

    registry.register("test.errors", good_handler, priority=10)
    registry.register("test.errors", bad_handler, priority=20)
    registry.register("test.errors", good_handler, priority=30)

    results = await registry.trigger("test.errors")
    assert results == ["good", "good"]
    assert results_log == ["good", "good"]


async def test_unregister(registry: HookRegistry) -> None:
    async def handler(**_: object) -> str:
        return "hit"

    registry.register("test.unreg", handler)
    assert registry.has_handlers("test.unreg")

    removed = registry.unregister("test.unreg", handler)
    assert removed is True
    assert not registry.has_handlers("test.unreg")


async def test_unregister_not_found(registry: HookRegistry) -> None:
    async def handler(**_: object) -> None:
        pass

    assert registry.unregister("nope", handler) is False


async def test_has_handlers(registry: HookRegistry) -> None:
    assert not registry.has_handlers("empty")

    async def handler(**_: object) -> None:
        pass

    registry.register("filled", handler)
    assert registry.has_handlers("filled")


async def test_get_hook_names(registry: HookRegistry) -> None:
    async def handler(**_: object) -> None:
        pass

    registry.register("hook.a", handler)
    registry.register("hook.b", handler)

    names = registry.get_hook_names()
    assert set(names) == {"hook.a", "hook.b"}


async def test_multiple_plugins_same_hook(registry: HookRegistry) -> None:
    results: list[str] = []

    async def billing_handler(**_: object) -> None:
        results.append("billing")

    async def audit_handler(**_: object) -> None:
        results.append("audit")

    registry.register("usage.record", billing_handler, plugin_id="billing", priority=10)
    registry.register("usage.record", audit_handler, plugin_id="audit", priority=20)

    await registry.trigger("usage.record", user_id="u1", cost_usd=0.01)
    assert results == ["billing", "audit"]
