"""Knowledge Tree backend-engine plugin: concept extractors.

Plugin ID: backend-engine-concept-extractor

Registers spaCy, LLM, and hybrid entity-extraction strategies under a
single plugin. Select the active strategy via ``settings.entity_extractor``.

Workers register this plugin explicitly at startup — see each service's
``__main__.py`` for the call to ``plugin_registry.register_backend_engine``.
"""
