"""Tests for the plugin entry point framework."""

from __future__ import annotations

import pytest

from kt_config.plugin import (
    BackendEnginePlugin,
    BackendPlugin,
    EntityExtractorContribution,
    FrontendPlugin,
    PluginDatabase,
    PluginRegistry,
    PluginType,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


class _NoOpPlugin(BackendEnginePlugin):
    plugin_id = "backend-engine-test-noop"


class _DBPlugin(BackendEnginePlugin):
    plugin_id = "backend-engine-test-db"

    def get_database(self) -> PluginDatabase:
        from pathlib import Path

        return PluginDatabase(
            plugin_id=self.plugin_id,
            schema_name="plugin_test",
            alembic_config_path=Path("/nonexistent/alembic.ini"),
        )


class _ExtractorPlugin(BackendEnginePlugin):
    plugin_id = "backend-engine-test-extractor"

    def get_entity_extractor(self) -> EntityExtractorContribution:
        return EntityExtractorContribution(
            extractor_name="test-extractor",
            factory=lambda _gw: object(),  # type: ignore[arg-type]
        )


# ── PluginType ────────────────────────────────────────────────────────────


def test_plugin_type_values() -> None:
    assert PluginType.backend_engine == "backend-engine"
    assert PluginType.backend == "backend"
    assert PluginType.frontend == "frontend"


# ── BackendEnginePlugin ABC ───────────────────────────────────────────────


def test_plugin_type_property() -> None:
    p = _NoOpPlugin()
    assert p.plugin_type == PluginType.backend_engine


def test_optional_entry_points_default_none() -> None:
    p = _NoOpPlugin()
    assert p.get_database() is None
    assert p.get_entity_extractor() is None


def test_stub_abcs_abstract() -> None:
    """BackendPlugin and FrontendPlugin cannot be instantiated directly."""
    with pytest.raises(TypeError):
        BackendPlugin()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        FrontendPlugin()  # type: ignore[abstract]


# ── PluginRegistry ────────────────────────────────────────────────────────


def test_register_backend_engine() -> None:
    registry = PluginRegistry()
    plugin = _NoOpPlugin()
    registry.register_backend_engine(plugin)
    assert len(registry.manifests) == 1


def test_register_idempotent() -> None:
    registry = PluginRegistry()
    registry.register_backend_engine(_NoOpPlugin())
    registry.register_backend_engine(_NoOpPlugin())  # same plugin_id
    assert len(registry.manifests) == 1


def test_register_multiple_distinct() -> None:
    registry = PluginRegistry()
    registry.register_backend_engine(_NoOpPlugin())
    registry.register_backend_engine(_DBPlugin())
    assert len(registry.manifests) == 2


def test_get_entity_extractor_found() -> None:
    registry = PluginRegistry()
    registry.register_backend_engine(_ExtractorPlugin())
    result = registry.get_entity_extractor("test-extractor", gateway=None)
    assert result is not None


def test_get_entity_extractor_not_found() -> None:
    registry = PluginRegistry()
    result = registry.get_entity_extractor("nonexistent", gateway=None)
    assert result is None


def test_get_entity_extractor_wrong_name() -> None:
    registry = PluginRegistry()
    registry.register_backend_engine(_ExtractorPlugin())
    result = registry.get_entity_extractor("other-name", gateway=None)
    assert result is None


@pytest.mark.asyncio
async def test_run_database_migrations_skips_no_db_plugins() -> None:
    """Plugins with no database entry point are silently skipped."""
    registry = PluginRegistry()
    registry.register_backend_engine(_NoOpPlugin())
    # Should not raise even with a dummy URL
    await registry.run_database_migrations(["postgresql+asyncpg://localhost/test"])


@pytest.mark.asyncio
async def test_run_database_migrations_swallows_errors() -> None:
    """Failed migrations are logged but never propagate."""
    registry = PluginRegistry()
    registry.register_backend_engine(_DBPlugin())  # nonexistent alembic.ini
    # Must not raise despite the broken config path
    await registry.run_database_migrations(["postgresql+asyncpg://localhost/test"])


@pytest.mark.asyncio
async def test_run_database_migrations_iterates_urls_and_dedupes() -> None:
    """Every registered plugin DB runs against every URL; duplicates collapse."""

    class _CountingPlugin(_DBPlugin):
        plugin_id = "backend-engine-test-count"
        calls: list[str] = []

        def get_database(self):
            class _DB(PluginDatabase):
                async def ensure_migrated(self, url: str, *, schema: str | None = None) -> None:  # type: ignore[override]
                    _CountingPlugin.calls.append(url)

            from pathlib import Path

            return _DB(
                plugin_id=self.plugin_id,
                schema_name="plugin_count",
                alembic_config_path=Path("/nonexistent"),
            )

    registry = PluginRegistry()
    registry.register_backend_engine(_CountingPlugin())
    await registry.run_database_migrations(["postgres://a", "postgres://b", "postgres://a"])
    assert _CountingPlugin.calls == ["postgres://a", "postgres://b"]


def test_clear_removes_all_plugins() -> None:
    registry = PluginRegistry()
    registry.register_backend_engine(_NoOpPlugin())
    registry.register_backend_engine(_DBPlugin())
    registry.clear()
    assert registry.manifests == []


def test_post_extraction_hook_lookup() -> None:
    """iter_post_extraction_hooks yields hooks matching the extractor name."""
    from kt_config.plugin import PostExtractionHook

    async def _handler(session, items, scope):  # type: ignore[no-untyped-def]
        pass

    class _HookPlugin(BackendEnginePlugin):
        plugin_id = "backend-engine-test-hook"

        def get_entity_extractor(self) -> EntityExtractorContribution:
            return EntityExtractorContribution(
                extractor_name="sinky",
                factory=lambda _gw: object(),  # type: ignore[arg-type]
            )

        def get_post_extraction_hooks(self):
            yield PostExtractionHook(
                extractor_name="sinky",
                output_key="shells",
                handler=_handler,
            )
            yield PostExtractionHook(
                extractor_name=None,
                output_key="rejected",
                handler=_handler,
            )

    registry = PluginRegistry()
    registry.register_backend_engine(_HookPlugin())

    sinky_hooks = list(registry.iter_post_extraction_hooks("sinky"))
    assert [h.output_key for h in sinky_hooks] == ["shells", "rejected"]

    other_hooks = list(registry.iter_post_extraction_hooks("other"))
    # extractor-agnostic hook fires for every extractor
    assert [h.output_key for h in other_hooks] == ["rejected"]
