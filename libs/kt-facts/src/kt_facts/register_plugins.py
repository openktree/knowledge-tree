"""Explicit plugin registration for kt-facts pipeline extensions.

Call ``register_kt_facts_plugins()`` in each worker's ``__main__.py``
**before** ``worker.start()`` so that:
1. Plugin DB migrations run during ``worker_lifespan`` startup.
2. ``DecompositionPipeline._make_extractor()`` can resolve plugin-provided
   extractor names (e.g. ``entity_extractor = "hybrid"`` in config.yaml).

Workers that do NOT use the decomposition pipeline do not need to call this.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register_kt_facts_plugins() -> None:
    """Register all backend-engine plugins that extend the kt-facts pipeline.

    Idempotent — safe to call multiple times (registry deduplicates by plugin_id).
    """
    from kt_config.plugin import plugin_registry

    try:
        from kt_plugin_be_hybrid_extractor.plugin import (
            HybridExtractorBackendEnginePlugin,
        )

        plugin_registry.register_backend_engine(HybridExtractorBackendEnginePlugin())
        logger.debug("Registered plugin: backend-engine-hybrid-extractor")
    except ImportError:
        logger.debug(
            "kt-plugin-be-hybrid-extractor not installed — hybrid extractor unavailable"
        )
