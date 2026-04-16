"""Core ``kt-db`` migrations packaged as a plugin.

Historically the core graph-db and write-db schemas were migrated by
``just migrate`` / a K8s Job. This module wraps both Alembic configs in
the same ``PluginDatabase`` contract used by third-party plugins so they
run through the single ``plugin_registry.run_database_migrations`` code
path at API / worker startup.
"""

from __future__ import annotations

from pathlib import Path

import kt_db
from kt_config.plugin import (
    BackendEnginePlugin,
    PluginDatabase,
    PluginRegistry,
)

#: kt_db/__init__.py -> src/kt_db -> kt-db/
_KT_DB_ROOT = Path(kt_db.__file__).resolve().parents[2]

CORE_GRAPH_DB_ALEMBIC_INI = _KT_DB_ROOT / "alembic.ini"
CORE_WRITE_DB_ALEMBIC_INI = _KT_DB_ROOT / "alembic_write.ini"


class CoreGraphDbPlugin(BackendEnginePlugin):
    """Owns the core graph-db ``public`` schema migrations."""

    plugin_id = "core-graph-db"

    def get_database(self) -> PluginDatabase:
        return PluginDatabase(
            plugin_id=self.plugin_id,
            schema_name="public",
            alembic_config_path=CORE_GRAPH_DB_ALEMBIC_INI,
            target="graph",
        )


class CoreWriteDbPlugin(BackendEnginePlugin):
    """Owns the core write-db ``public`` schema migrations."""

    plugin_id = "core-write-db"

    def get_database(self) -> PluginDatabase:
        return PluginDatabase(
            plugin_id=self.plugin_id,
            schema_name="public",
            alembic_config_path=CORE_WRITE_DB_ALEMBIC_INI,
            target="write",
        )


def register_core_plugins(registry: PluginRegistry) -> None:
    """Register core kt-db plugins. Idempotent."""
    registry.register_backend_engine(CoreGraphDbPlugin())
    registry.register_backend_engine(CoreWriteDbPlugin())
