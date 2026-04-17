"""FlagEvalContextMiddleware smoke tests.

Raw-ASGI middleware must:
  - Attach ``flag_context`` to request state.
  - Pass streaming (SSE-style) responses through without buffering — the
    project's synthesis workflows depend on this.
  - Be resilient to missing auth state (Phase 0 — fastapi-users sets user
    via Depends which fires after middleware).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from kt_flags.fastapi import FlagEvalContextMiddleware


def _build_app() -> Starlette:
    async def state_probe(request: Request) -> JSONResponse:
        ctx = request.state.flag_context
        return JSONResponse(
            {
                "has_context": ctx is not None,
                "targeting_key": ctx.targeting_key,
                "graph_id": ctx.attributes.get("graph_id"),
            }
        )

    async def streamer(_request: Request) -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            for i in range(3):
                yield f"chunk-{i}\n".encode()
                await asyncio.sleep(0)

        return StreamingResponse(gen(), media_type="text/event-stream")

    app = Starlette(
        routes=[
            Route("/state", state_probe),
            Route("/stream", streamer),
            Route("/graphs/{graph_id}/state", state_probe),
        ]
    )
    app.add_middleware(FlagEvalContextMiddleware)
    return app


def test_middleware_attaches_empty_context_by_default() -> None:
    with TestClient(_build_app()) as client:
        r = client.get("/state")
    assert r.status_code == 200
    body = r.json()
    assert body["has_context"] is True
    assert body["targeting_key"] is None
    assert body["graph_id"] is None


def test_middleware_extracts_graph_id_from_path() -> None:
    with TestClient(_build_app()) as client:
        r = client.get("/graphs/g-abc/state")
    assert r.status_code == 200
    assert r.json()["graph_id"] == "g-abc"


def test_middleware_extracts_graph_id_from_header() -> None:
    with TestClient(_build_app()) as client:
        r = client.get("/state", headers={"X-Graph-Id": "g-hdr"})
    assert r.status_code == 200
    assert r.json()["graph_id"] == "g-hdr"


def test_streaming_response_not_buffered() -> None:
    """Raw-ASGI middleware must not break SSE streaming."""
    with TestClient(_build_app()) as client:
        with client.stream("GET", "/stream") as r:
            chunks = [chunk for chunk in r.iter_raw() if chunk]
    joined = b"".join(chunks)
    assert b"chunk-0\n" in joined
    assert b"chunk-1\n" in joined
    assert b"chunk-2\n" in joined


@pytest.mark.parametrize("path", ["/state", "/graphs/g-abc/state"])
def test_middleware_does_not_require_user_state(path: str) -> None:
    """Auth runs as a Depends after middleware — user is absent, that's fine."""
    with TestClient(_build_app()) as client:
        r = client.get(path)
    assert r.status_code == 200
    assert r.json()["targeting_key"] is None
