"""Core backend-engine contracts shared by plugins and the core pipelines.

Pure abstractions and helpers only — no orchestration, no DB, no LLM, no
network. Plugins depend on ``kt-core-engine-api`` to avoid pulling concrete
implementation libs (``kt-facts``, ``kt-providers``, …).

Subpackages:
- ``kt_core_engine_api.extractor`` — entity-extraction ABC + types
- ``kt_core_engine_api.search`` — knowledge-provider search ABC + types
"""
