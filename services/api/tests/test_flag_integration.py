"""End-to-end proof that migrated call sites read through the flag client.

Flips ``feature.full_text_fetch`` via ``override_flags`` and asserts the
``get_agent_context`` dependency in ``kt_api.dependencies`` honors it by
omitting the fetch registry when the flag is off.

No DB / HTTP server required — we only exercise the dependency function's
branch on the flag value.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kt_flags.testing import override_flags


@pytest.mark.asyncio
async def test_fetch_registry_gated_by_flag() -> None:
    from kt_api import dependencies

    async def _fake_emit(*_args: object, **_kwargs: object) -> None:
        return None

    with (
        patch.object(dependencies, "get_session_factory_cached") as mock_sf,
        patch.object(dependencies, "get_qdrant_client_cached") as mock_q,
        patch.object(dependencies, "ReadGraphEngine") as mock_read,
        patch.object(dependencies, "EmbeddingService") as mock_embed,
        patch.object(dependencies, "ModelGateway"),
        patch.object(dependencies, "ProviderRegistry") as mock_reg,
        patch.object(dependencies, "iter_extra_provider_factories", return_value=iter([])),
        patch.object(dependencies, "build_fetch_registry") as mock_build_fetch,
    ):
        session = object()
        mock_sf.return_value = lambda: session
        mock_q.return_value = object()
        mock_read.return_value = object()
        mock_embed.return_value = object()
        mock_reg.return_value = object()

        # Flag OFF — build_fetch_registry must not be called
        with override_flags({"feature.full_text_fetch": False}):
            ctx_off = await dependencies.get_agent_context(emit_event=_fake_emit)
        assert ctx_off.fetch_registry is None
        assert mock_build_fetch.call_count == 0

        # Flag ON — build_fetch_registry is called
        mock_build_fetch.return_value = "fake-registry"
        with override_flags({"feature.full_text_fetch": True}):
            ctx_on = await dependencies.get_agent_context(emit_event=_fake_emit)
        assert ctx_on.fetch_registry == "fake-registry"
        assert mock_build_fetch.call_count == 1
