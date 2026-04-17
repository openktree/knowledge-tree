"""HookRegistry — trigger, filter, priority, error isolation, fire-and-forget."""

from __future__ import annotations

import asyncio

from kt_plugins.hooks import HookRegistry


async def test_trigger_fires_all_handlers_in_priority_order() -> None:
    reg = HookRegistry()
    order: list[str] = []

    async def late(**_: object) -> str:
        order.append("late")
        return "late"

    async def early(**_: object) -> str:
        order.append("early")
        return "early"

    reg.register("ev", late, priority=200)
    reg.register("ev", early, priority=50)

    results = await reg.trigger("ev", x=1)

    assert order == ["early", "late"]
    assert results == ["early", "late"]


async def test_trigger_swallows_handler_exceptions() -> None:
    reg = HookRegistry()

    async def boom(**_: object) -> None:
        raise RuntimeError("kaboom")

    async def ok(**_: object) -> str:
        return "ok"

    reg.register("ev", boom, priority=10)
    reg.register("ev", ok, priority=20)

    results = await reg.trigger("ev")
    assert results == ["ok"]  # boom's exception does not abort the chain


async def test_filter_chains_value() -> None:
    reg = HookRegistry()

    async def add_one(v: int, **_: object) -> int:
        return v + 1

    async def times_two(v: int, **_: object) -> int:
        return v * 2

    reg.register("f", add_one, priority=10)
    reg.register("f", times_two, priority=20)

    # (3 + 1) * 2 = 8
    assert await reg.filter("f", 3) == 8


async def test_filter_skips_exploding_handler_but_keeps_value() -> None:
    reg = HookRegistry()

    async def boom(v: int, **_: object) -> int:
        raise ValueError("x")

    async def plus(v: int, **_: object) -> int:
        return v + 10

    reg.register("f", boom, priority=10)
    reg.register("f", plus, priority=20)

    assert await reg.filter("f", 1) == 11


async def test_unregister_removes_handler() -> None:
    reg = HookRegistry()

    async def h(**_: object) -> str:
        return "h"

    reg.register("ev", h)
    assert reg.unregister("ev", h) is True
    assert await reg.trigger("ev") == []
    assert reg.unregister("ev", h) is False  # already gone


async def test_fire_and_forget_does_not_block_caller() -> None:
    reg = HookRegistry()
    gate = asyncio.Event()
    seen: list[int] = []

    async def slow(**kwargs: object) -> None:
        await gate.wait()
        seen.append(int(kwargs["n"]))

    reg.register("ev", slow)

    # fire_and_forget returns immediately; handler sits on gate.
    reg.fire_and_forget("ev", n=1)
    assert seen == []

    gate.set()
    # Yield the loop so the detached task gets to run.
    for _ in range(10):
        await asyncio.sleep(0)
        if seen:
            break
    assert seen == [1]


def test_fire_and_forget_without_running_loop_is_noop() -> None:
    reg = HookRegistry()

    async def _h(**_: object) -> None:
        raise AssertionError("must not run")

    reg.register("ev", _h)
    # Called from sync context — no loop running. Must not raise.
    reg.fire_and_forget("ev")


def test_clear_resets_state() -> None:
    reg = HookRegistry()

    async def h(**_: object) -> None: ...

    reg.register("a", h)
    reg.register("b", h)
    reg.clear("a")
    assert not reg.has_handlers("a")
    assert reg.has_handlers("b")
    reg.clear()
    assert not reg.has_handlers("b")


async def test_has_handlers_and_get_hook_names() -> None:
    reg = HookRegistry()

    async def h(**_: object) -> None: ...

    reg.register("x", h)
    assert reg.has_handlers("x") is True
    assert reg.has_handlers("y") is False
    assert "x" in reg.get_hook_names()
