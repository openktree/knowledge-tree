"""Core backend-engine contracts shared by plugins and the core pipelines.

Pure abstractions and helpers only — no orchestration, no DB, no LLM, no
network. Plugins depend on ``kt-core-engine-api`` to avoid pulling concrete
implementation libs (``kt-facts``, ``kt-providers``, …).

Subpackages:
- ``kt_core_engine_api.extractor`` — entity-extraction ABC + types
- ``kt_core_engine_api.search`` — knowledge-provider search ABC + types

Modules:
- ``kt_core_engine_api.services`` — Backstage-style ``CoreServices`` /
  ``PipelineContext`` factory used by Hatchet tasks and providers.
"""

from kt_core_engine_api.services import CoreServices, GraphConfig, PipelineContext

__all__ = ["CoreServices", "GraphConfig", "PipelineContext"]
