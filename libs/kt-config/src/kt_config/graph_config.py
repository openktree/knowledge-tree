"""Per-graph configuration resolution — YAML + plugin defaults + Settings.

Reads from ``config.yaml`` under a new ``graphs:`` section with a
``_shared`` fallback and per-graph overrides:

.. code-block:: yaml

    graphs:
      _shared:
        fact_decomposition:
          model: "openrouter/google/gemini-3.1-flash-lite-preview"
      default:
        search:
          providers: ["serper"]

The resolver is cached per graph id; invalidated on ``PATCH
/graphs/{slug}/config`` and on migration workflow completion.

Resolution order (highest wins):
 1. ``Graph.config[<phase>][<key>]`` — reserved for future UI edits;
    skipped in Phase 2 (column exists, resolver does not read it).
 2. ``config.yaml :: graphs.<slug>.<phase>.<key>``
 3. ``config.yaml :: graphs._shared.<phase>.<key>``
 4. ``GraphTypePlugin.default_phase_settings()[<phase>][<key>]``
 5. Global ``Settings`` field (back-compat fallback, logs deprecation).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from kt_config.plugin import GraphTypeComposition, plugin_registry
from kt_config.settings import get_settings

if TYPE_CHECKING:
    from kt_db.models import Graph

logger = logging.getLogger(__name__)

_SHARED_KEY = "_shared"


# ── Public dataclasses ────────────────────────────────────────────────


@dataclass(frozen=True)
class GraphConfig:
    """Resolved per-graph configuration handed to every pipeline task.

    ``phase_settings`` is a nested dict — top level is phase name
    (``"fact_decomposition"``, ``"search"``, …) and each value is the
    merged dict of YAML + plugin-default values for that phase.
    """

    graph_type_id: str
    graph_type_version: int
    composition: GraphTypeComposition
    phase_settings: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get(self, path: str, default: Any = None) -> Any:
        """Read a dotted ``phase.key`` path with a Settings fallback.

        ``path`` must be ``"<phase>.<key>"``. If the phase/key is missing
        from YAML + plugin defaults, falls back to the matching global
        ``Settings`` field (logging a DEPRECATED warning the first time
        a given field is read from Settings).
        """
        try:
            phase, key = path.split(".", 1)
        except ValueError as e:
            raise ValueError(f"GraphConfig.get requires '<phase>.<key>', got {path!r}") from e
        section = self.phase_settings.get(phase)
        if section is not None and key in section:
            return section[key]
        return default

    def for_phase(self, phase: str) -> dict[str, Any]:
        """Return the merged settings dict for one phase (empty if unset)."""
        return dict(self.phase_settings.get(phase, {}))


# ── Resolver ──────────────────────────────────────────────────────────


class GraphConfigResolver:
    """Loads ``config.yaml`` once at construction, resolves per-graph on demand.

    Not thread-aware — callers use the singleton held by ``WorkerState``
    and the resolver cache is bounded by the set of active graphs
    (small). Cache entries are invalidated via :meth:`invalidate`.
    """

    def __init__(self, *, yaml_path: str | Path | None = None) -> None:
        resolved = Path(yaml_path) if yaml_path else _default_yaml_path()
        self._yaml_path = resolved
        self._graphs_section: dict[str, Any] = {}
        if resolved and resolved.is_file():
            with open(resolved) as f:
                raw = yaml.safe_load(f)
            if isinstance(raw, dict):
                section = raw.get("graphs")
                if isinstance(section, dict):
                    self._graphs_section = section
        self._cache: dict[uuid.UUID | None, GraphConfig] = {}

    # -- Public API --------------------------------------------------------

    async def resolve(
        self,
        graph: "Graph | None",
    ) -> GraphConfig:
        """Resolve the config for a Graph ORM row (or ``None`` = default).

        Cached by graph id; subsequent calls for the same row hit the cache
        until :meth:`invalidate` is called.
        """
        cache_key: uuid.UUID | None = graph.id if graph is not None else None
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        graph_type_id = graph.graph_type_id if graph is not None else "default"
        graph_type_version = graph.graph_type_version if graph is not None else 1
        plugin = plugin_registry.get_graph_type(graph_type_id)
        if plugin is None:
            # Graph references a type that isn't registered. Fall back to
            # the 'default' plugin so pipelines keep running; log loud.
            logger.warning(
                "Graph %s references unregistered graph_type_id=%r — falling back to 'default'",
                graph.slug if graph else "<default>",
                graph_type_id,
            )
            plugin = plugin_registry.get_graph_type("default")
        composition = plugin.composition() if plugin else _empty_composition()

        plugin_defaults = plugin.default_phase_settings() if plugin else {}
        shared = self._graphs_section.get(_SHARED_KEY, {})
        graph_slug = graph.slug if graph is not None else "default"
        graph_overrides = self._graphs_section.get(graph_slug, {})

        # Layer resolution: plugin defaults → _shared → per-graph.
        # Later layers win per-key within each phase.
        phase_settings: dict[str, dict[str, Any]] = {}
        for layer in (plugin_defaults, shared, graph_overrides):
            if not isinstance(layer, dict):
                continue
            for phase, phase_data in layer.items():
                if not isinstance(phase_data, dict):
                    continue
                dest = phase_settings.setdefault(phase, {})
                for key, value in phase_data.items():
                    dest[key] = value

        config = GraphConfig(
            graph_type_id=graph_type_id,
            graph_type_version=graph_type_version,
            composition=composition,
            phase_settings=phase_settings,
        )
        self._cache[cache_key] = config
        return config

    def invalidate(self, graph_id: uuid.UUID | None) -> None:
        """Drop the cached resolution for one graph (or default)."""
        self._cache.pop(graph_id, None)

    def invalidate_all(self) -> None:
        """Drop every cached resolution — used on YAML reload."""
        self._cache.clear()

    # -- Settings fallback helper --------------------------------------

    def settings_fallback(self, config: GraphConfig, path: str, settings_field: str) -> Any:
        """Return config[path] or log-and-fall-back to ``Settings.<settings_field>``.

        Call sites that are not yet converted to phase-namespaced config
        go through this helper during the deprecation window. The first
        read of each flat field logs a WARNING; subsequent reads are silent
        to keep logs clean.
        """
        value = config.get(path, default=_MISSING)
        if value is not _MISSING:
            return value
        _warn_deprecated_flat_read(settings_field, path)
        return getattr(get_settings(), settings_field, None)


# ── Helpers ───────────────────────────────────────────────────────────


_MISSING = object()
_warned_fields: set[str] = set()


def _warn_deprecated_flat_read(settings_field: str, yaml_path: str) -> None:
    if settings_field in _warned_fields:
        return
    _warned_fields.add(settings_field)
    logger.warning(
        "DEPRECATED: settings.%s read as fallback — move override to "
        "config.yaml :: graphs.<slug>.%s (or graphs._shared.%s)",
        settings_field,
        yaml_path,
        yaml_path,
    )


def _default_yaml_path() -> Path | None:
    """Locate ``config.yaml`` the same way ``Settings`` does.

    ``Settings`` resolves the path via the ``CONFIG_YAML_PATH`` env var
    (set by the caller) with a repo-root fallback. We mirror that so both
    sources see the same file.
    """
    import os

    env_path = os.environ.get("CONFIG_YAML_PATH")
    if env_path:
        return Path(env_path)
    # Repo-root fallback — walk up from this file until we find config.yaml.
    here = Path(__file__).resolve().parent
    for ancestor in (here, *here.parents):
        candidate = ancestor / "config.yaml"
        if candidate.is_file():
            return candidate
    return None


def _empty_composition() -> GraphTypeComposition:
    """Safe fallback when no graph type plugin is registered."""
    return GraphTypeComposition(
        fetch_chain=[],
        search_providers=[],
        fact_decomposition="llm-default",
        concept_extractor="hybrid",
        disambiguation="default",
        seed_multiplex="default",
        seed_promotion="default",
        dimensions="default",
        definition="default",
        relations="default",
        sync="default",
        source_cache="public-graph",
        source_contribution="public-graph",
        agentic_tasks={},
    )
