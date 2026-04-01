import os
from pathlib import Path
from typing import Any

import yaml
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]

_DEFAULT_YAML_PATH = str(_PROJECT_ROOT / "config.yaml")


# Canonical mapping: Settings field name → (section, yaml_key).
# Sections are required — every field must belong to exactly one section.
# The yaml_key is the short name used inside the section (prefix stripped
# where it would be redundant with the section name).
_FIELD_SECTION_MAP: dict[str, tuple[str, str]] = {}

# Reverse index built lazily: (section, yaml_key) → field_name
_YAML_KEY_TO_FIELD: dict[tuple[str, str], str] = {}


def _register(section: str, fields: dict[str, str]) -> None:
    """Register a batch of field_name → yaml_key mappings for a section."""
    for field_name, yaml_key in fields.items():
        _FIELD_SECTION_MAP[field_name] = (section, yaml_key)
        _YAML_KEY_TO_FIELD[(section, yaml_key)] = field_name


# ---- Infrastructure --------------------------------------------------------
_register(
    "infrastructure",
    {
        "database_url": "database_url",
        "db_pool_size": "db_pool_size",
        "db_max_overflow": "db_max_overflow",
        "db_pool_timeout": "db_pool_timeout",
        "write_database_url": "write_database_url",
        "write_db_pool_size": "write_db_pool_size",
        "write_db_max_overflow": "write_db_max_overflow",
        "write_db_pool_timeout": "write_db_pool_timeout",
        "qdrant_url": "qdrant_url",
        "redis_url": "redis_url",
        "sync_interval_seconds": "sync_interval_seconds",
        "sync_batch_size": "sync_batch_size",
        "sync_max_retries": "sync_max_retries",
        "sync_retry_base_seconds": "sync_retry_base_seconds",
        "log_level": "log_level",
        "pipeline_concurrency": "pipeline_concurrency",
    },
)

# ---- Auth ------------------------------------------------------------------
_register(
    "auth",
    {
        "jwt_secret_key": "jwt_secret_key",
        "access_token_expire_minutes": "access_token_expire_minutes",
        "refresh_token_expire_days": "refresh_token_expire_days",
        "skip_auth": "skip_auth",
        "google_oauth_client_id": "google_oauth_client_id",
        "google_oauth_client_secret": "google_oauth_client_secret",
        "byok_encryption_key": "byok_encryption_key",
        "mcp_oauth_base_url": "mcp_oauth_base_url",
    },
)

# ---- API keys (secrets — prefer .env) --------------------------------------
_register(
    "api_keys",
    {
        "openrouter_api_key": "openrouter",
        "brave_key": "brave",
        "serper_key": "serper",
        "openai_api_key": "openai",
    },
)

# ---- Models ----------------------------------------------------------------
_register(
    "models",
    {
        "default_model": "default",
        "default_thinking_level": "default_thinking_level",
        "enable_secondary_models": "enable_secondary_models",
    },
)

# ---- Embeddings ------------------------------------------------------------
_register(
    "embeddings",
    {
        "embedding_model": "model",
        "embedding_dimensions": "dimensions",
        "embedding_timeout": "timeout",
        "embedding_batch_chunk_size": "batch_chunk_size",
    },
)

# ---- Search ----------------------------------------------------------------
_register(
    "search",
    {
        "default_search_provider": "provider",
        "enable_full_text_fetch": "enable_full_text_fetch",
        "full_text_fetch_max_urls": "full_text_fetch_max_urls",
        "full_text_fetch_timeout": "full_text_fetch_timeout",
        "page_stale_days": "page_stale_days",
        "page_fetch_max_extra_pages": "page_fetch_max_extra_pages",
        "full_text_fetch_per_budget_point": "full_text_fetch_per_budget_point",
        "fetch_guarantee_max_rounds": "fetch_guarantee_max_rounds",
    },
)

# ---- Orchestrator ----------------------------------------------------------
_register(
    "orchestrator",
    {
        "orchestrator_model": "model",
        "orchestrator_thinking_level": "thinking_level",
        "scope_model": "scope_model",
        "scope_thinking_level": "scope_thinking_level",
        "agent_select_model": "agent_select_model",
        "agent_select_thinking_level": "agent_select_thinking_level",
        "agent_select_concurrency": "agent_select_concurrency",
        "prioritization_model": "prioritization_model",
        "default_nav_budget": "nav_budget",
        "default_explore_budget": "explore_budget",
        "default_wave_count": "wave_count",
        "enable_semantic_expansion": "enable_semantic_expansion",
        "semantic_expansion_max_terms": "semantic_expansion_max_terms",
        "semantic_expansion_fact_threshold": "semantic_expansion_fact_threshold",
        "agent_inactivity_timeout_seconds": "inactivity_timeout_seconds",
        "scope_timeout_seconds": "scope_timeout_seconds",
        "hatchet_execution_timeout_minutes": "hatchet_execution_timeout_minutes",
        "hatchet_schedule_timeout_minutes": "hatchet_schedule_timeout_minutes",
        "use_hatchet": "use_hatchet",
    },
)

# ---- Decomposition ---------------------------------------------------------
_register(
    "decomposition",
    {
        "decomposition_model": "model",
        "decomposition_thinking_level": "thinking_level",
        "file_decomposition_model": "file_model",
        "file_decomposition_thinking_level": "file_thinking_level",
        "entity_extraction_model": "entity_extraction_model",
        "entity_extraction_thinking_level": "entity_extraction_thinking_level",
        "entity_extraction_batch_size": "entity_extraction_batch_size",
        "entity_extraction_concurrency": "entity_extraction_concurrency",
        "fact_pool_threshold": "fact_pool_threshold",
        "default_max_content_tokens": "max_content_tokens",
        "default_stale_after_days": "stale_after_days",
        "llm_call_timeout_seconds": "llm_call_timeout_seconds",
        "super_source_token_threshold": "super_source_token_threshold",
        "super_source_page_threshold": "super_source_page_threshold",
    },
)

# ---- Synthesis -------------------------------------------------------------
_register(
    "synthesis",
    {
        "synthesis_model": "model",
        "synthesis_thinking_level": "thinking_level",
        "chat_model": "chat_model",
        "chat_thinking_level": "chat_thinking_level",
        "query_agent_model": "query_agent_model",
    },
)

# ---- Node pipeline ---------------------------------------------------------
_register(
    "node_pipeline",
    {
        "dimension_model": "dimension_model",
        "dimension_thinking_level": "dimension_thinking_level",
        "dimension_fact_limit": "dimension_fact_limit",
        "dimension_saturation_ratio": "dimension_saturation_ratio",
        "dimension_pool_multiplier": "dimension_pool_multiplier",
        "definition_model": "definition_model",
        "definition_thinking_level": "definition_thinking_level",
    },
)

# ---- Edges -----------------------------------------------------------------
_register(
    "edges",
    {
        "edge_resolution_model": "resolution_model",
        "edge_resolution_thinking_level": "resolution_thinking_level",
        "relation_dedup_threshold": "relation_dedup_threshold",
        "edge_staleness_days": "staleness_days",
        "edge_classification_batch_size": "classification_batch_size",
        "edge_facts_per_type_cap": "facts_per_type_cap",
        "edge_facts_per_candidate_cap": "facts_per_candidate_cap",
        "parent_selection_model": "parent_selection_model",
        "parent_selection_thinking_level": "parent_selection_thinking_level",
    },
)

# ---- Ontology --------------------------------------------------------------
_register(
    "ontology",
    {
        "ontology_model": "model",
        "ontology_cache_ttl": "cache_ttl",
        "enable_ontology_ancestry": "enable_ancestry",
        "ontology_similarity_threshold": "similarity_threshold",
        "wikidata_user_agent": "wikidata_user_agent",
        "crystallization_child_threshold": "crystallization_child_threshold",
        "crystallization_child_change_ratio": "crystallization_child_change_ratio",
        "crystallization_model": "crystallization_model",
        "crystallization_thinking_level": "crystallization_thinking_level",
    },
)

# ---- Email -----------------------------------------------------------------
_register(
    "email",
    {
        "email_enabled": "enabled",
        "email_provider": "provider",
        "email_verification": "verification",
        "email_from_address": "from_address",
        "resend_api_key": "resend_api_key",
    },
)

# ---- Ingest ----------------------------------------------------------------
_register(
    "ingest",
    {
        "ingest_upload_dir": "upload_dir",
        "ingest_max_file_size_mb": "max_file_size_mb",
        "ingest_short_content_threshold": "short_content_threshold",
        "import_cleanup_batch_size": "import_cleanup_batch_size",
    },
)

# ---- Graph building (automated, no LLM) ------------------------------------
_register(
    "graph_building",
    {
        "graph_build_auto_promote_min_facts": "auto_promote_min_facts",
        "graph_build_edge_min_shared_facts": "edge_min_shared_facts",
        "graph_build_batch_size": "batch_size",
        "graph_build_auto_recalculate_min_new_facts": "auto_recalculate_min_new_facts",
        "graph_build_auto_recalculate_batch_size": "auto_recalculate_batch_size",
    },
)

# ---- On-demand enrichment --------------------------------------------------
_register(
    "enrichment",
    {
        "enrichment_min_facts_for_dimensions": "min_facts_for_dimensions",
        "enrichment_access_count_trigger": "access_count_trigger",
        "enrichment_dimension_sample_size": "dimension_sample_size",
        "enrichment_edge_justification_sample_size": "edge_justification_sample_size",
    },
)

# ---- Hatchet concurrency ---------------------------------------------------
_register(
    "hatchet_concurrency",
    {
        "bottom_up_max_runs": "bottom_up_max_runs",
        "bottom_up_prepare_max_runs": "bottom_up_prepare_max_runs",
        "agent_select_max_runs": "agent_select_max_runs",
        "worker_bottomup_slots": "worker_bottomup_slots",
        "worker_bottomup_durable_slots": "worker_bottomup_durable_slots",
    },
)

# ---- Seeds -----------------------------------------------------------------
_register(
    "seeds",
    {
        "seed_dedup_embedding_threshold": "dedup_embedding_threshold",
        "seed_dedup_trigram_threshold": "dedup_trigram_threshold",
        "seed_disambiguation_fact_threshold": "disambiguation_fact_threshold",
        "seed_disambiguation_cluster_threshold": "disambiguation_cluster_threshold",
        "seed_promotion_min_facts": "promotion_min_facts",
        "seed_routing_embedding_threshold": "routing_embedding_threshold",
        "seed_routing_llm_ambiguity_margin": "routing_llm_ambiguity_margin",
        "seed_phonetic_trigram_threshold": "phonetic_trigram_threshold",
        "seed_dedup_typo_floor": "dedup_typo_floor",
        "seed_re_embed_thresholds": "re_embed_thresholds",
        "seed_dedup_auto_merge_threshold": "dedup_auto_merge_threshold",
        "seed_dedup_llm_model": "dedup_llm_model",
    },
)


class YamlSettingsSource(PydanticBaseSettingsSource):
    """Load settings from a sectioned YAML file.

    Priority: env vars > .env file > YAML > Python defaults.

    The YAML file is organized into required sections::

        orchestrator:
          model: "openrouter/minimax/minimax-m2.5:nitro"
          thinking_level: ""
          nav_budget: 200

    Each ``(section, yaml_key)`` pair maps to exactly one Settings field
    via ``_YAML_KEY_TO_FIELD``.  This avoids collisions — two sections
    can each have a ``model`` key without conflict.
    """

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: str) -> None:
        super().__init__(settings_cls)
        self._yaml_data: dict[str, Any] = {}
        path = Path(yaml_path)
        if path.is_file():
            with open(path) as f:
                raw = yaml.safe_load(f)
            if isinstance(raw, dict):
                self._yaml_data = self._resolve(raw)

    @staticmethod
    def _resolve(raw: dict[str, Any]) -> dict[str, Any]:
        """Map sectioned YAML keys to flat Settings field names."""
        resolved: dict[str, Any] = {}
        for section, section_data in raw.items():
            if not isinstance(section_data, dict):
                continue
            for yaml_key, value in section_data.items():
                field_name = _YAML_KEY_TO_FIELD.get((section, yaml_key))
                if field_name is not None:
                    resolved[field_name] = value
        return resolved

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        val = self._yaml_data.get(field_name)
        return val, field_name, self.field_is_complex(field)

    def __call__(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for field_name, field_info in self.settings_cls.model_fields.items():
            val, _, _ = self.get_field_value(field_info, field_name)
            if val is not None:
                d[field_name] = val
        return d


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://kt:localdev@localhost:5432/knowledge_tree"

    # External APIs
    openrouter_api_key: str = ""
    brave_key: str = ""
    serper_key: str = ""
    openai_api_key: str = ""

    # Defaults
    default_search_provider: str = "serper"  # "serper", "brave", or "all"
    default_nav_budget: int = 200
    default_explore_budget: int = 20
    default_max_content_tokens: int = 500
    default_stale_after_days: int = 30
    super_source_token_threshold: int = 70_000  # ~280K chars; sources above this are deferred
    super_source_page_threshold: int = 50  # PDFs above this page count are deferred
    embedding_model: str = "openrouter/openai/text-embedding-3-large"
    embedding_dimensions: int = 3072
    embedding_timeout: int = 120  # seconds; higher for OpenRouter proxy overhead
    embedding_batch_chunk_size: int = 32  # texts per API call; smaller = faster per-call through proxy
    pipeline_concurrency: int = 10

    # Fact pool
    fact_pool_threshold: float = 0.40

    # Dimension batching & definitions
    dimension_fact_limit: int = 60
    dimension_saturation_ratio: float = 0.7
    dimension_pool_multiplier: int = 2
    definition_model: str = ""
    definition_thinking_level: str = ""

    # Edge settings
    relation_dedup_threshold: float = 0.15
    default_model: str = "openrouter/x-ai/grok-4.1-fast"

    # Per-agent model overrides (empty string = use default_model)
    file_decomposition_model: str = ""
    decomposition_model: str = "openrouter/google/gemini-3.1-flash-lite-preview"
    entity_extraction_model: str = ""  # empty = use decomposition_model
    entity_extraction_thinking_level: str = ""
    entity_extraction_batch_size: int = 10
    entity_extraction_concurrency: int = 4
    synthesis_model: str = ""
    dimension_model: str = ""
    chat_model: str = ""
    orchestrator_model: str = ""
    scope_model: str = ""  # empty = use orchestrator_model
    agent_select_model: str = ""  # empty = use orchestrator_model
    agent_select_thinking_level: str = ""
    agent_select_concurrency: int = 10  # max parallel LLM calls for agent select batches
    prioritization_model: str = ""  # empty = use default_model
    query_agent_model: str = ""  # empty = use chat_model

    # Per-role thinking/reasoning effort (empty string = don't send parameter)
    # Valid values: "none", "low", "medium", "high" (model-dependent)
    default_thinking_level: str = ""
    decomposition_thinking_level: str = "low"
    file_decomposition_thinking_level: str = ""
    synthesis_thinking_level: str = ""
    dimension_thinking_level: str = ""
    chat_thinking_level: str = ""
    orchestrator_thinking_level: str = ""
    scope_thinking_level: str = ""

    # Ontology ancestry
    qdrant_url: str = "http://localhost:6333"
    redis_url: str = "redis://localhost:6379/0"
    ontology_cache_ttl: int = 604800  # 7 days in seconds
    ontology_model: str = "openrouter/x-ai/grok-4.1-fast"
    wikidata_user_agent: str = "KnowledgeTree/1.0 (example@openktree.com)"
    enable_ontology_ancestry: bool = True
    ontology_similarity_threshold: float = 0.82  # embedding threshold for matching existing nodes

    # Ontology crystallization
    crystallization_child_threshold: int = 10
    crystallization_child_change_ratio: float = 0.5
    crystallization_model: str = ""  # empty = use ontology_model
    crystallization_thinking_level: str = ""

    # Feature flags
    use_hatchet: bool = True  # True=Hatchet task queue, False=BackgroundTasks
    enable_secondary_models: bool = False
    enable_full_text_fetch: bool = True
    full_text_fetch_max_urls: int = 10
    full_text_fetch_timeout: float = 15.0

    # Page fetch dedup — skip URLs already processed within this window
    page_stale_days: int = 30
    page_fetch_max_extra_pages: int = 3  # max search pagination rounds to backfill skipped URLs

    # Guarantee loop: min fully-fetched pages per budget point
    full_text_fetch_per_budget_point: int = 5  # target fetched pages per budget point
    fetch_guarantee_max_rounds: int = 4  # max search rounds per budget point

    # Edge resolution agent
    edge_staleness_days: int = 30
    edge_resolution_model: str = ""
    edge_facts_per_type_cap: int = 20
    edge_facts_per_candidate_cap: int = 40
    edge_classification_batch_size: int = 5
    edge_resolution_thinking_level: str = ""
    parent_selection_model: str = ""
    parent_selection_thinking_level: str = ""

    # Semantic expansion (Phase 3)
    enable_semantic_expansion: bool = True
    semantic_expansion_max_terms: int = 15
    semantic_expansion_fact_threshold: float = 0.4

    # Import cleanup
    import_cleanup_batch_size: int = 20

    # Ingest
    ingest_upload_dir: str = "uploads"
    ingest_max_file_size_mb: int = 50
    ingest_short_content_threshold: int = 32000

    # Graph building (automated, no LLM)
    graph_build_auto_promote_min_facts: int = 10
    graph_build_edge_min_shared_facts: int = 3
    graph_build_batch_size: int = 100
    graph_build_auto_recalculate_min_new_facts: int = 10
    graph_build_auto_recalculate_batch_size: int = 20

    # On-demand enrichment
    enrichment_min_facts_for_dimensions: int = 100
    enrichment_access_count_trigger: int = 5
    enrichment_dimension_sample_size: int = 200
    enrichment_edge_justification_sample_size: int = 50

    # Seeds
    seed_dedup_embedding_threshold: float = 0.82
    seed_dedup_trigram_threshold: float = 0.50
    seed_disambiguation_fact_threshold: int = 10
    seed_disambiguation_cluster_threshold: float = 0.85
    seed_promotion_min_facts: int = 10
    seed_routing_embedding_threshold: float = 0.80
    seed_routing_llm_ambiguity_margin: float = 0.05
    seed_phonetic_trigram_threshold: float = 0.40
    seed_dedup_typo_floor: float = 0.75  # min embedding sim for phonetic+trigram typo merges
    seed_re_embed_thresholds: str = "5,15,50,100"
    seed_dedup_auto_merge_threshold: float = 0.95  # above this + guards → skip LLM
    seed_dedup_llm_model: str = ""  # empty = use decomposition_model (cheapest)

    # Wave pipeline
    default_wave_count: int = 2

    # Hatchet concurrency limits
    bottom_up_max_runs: int = 3
    bottom_up_prepare_max_runs: int = 3
    agent_select_max_runs: int = 3
    worker_bottomup_slots: int = 20
    worker_bottomup_durable_slots: int = 40

    # Timeouts
    agent_inactivity_timeout_seconds: int = 300  # stall detection: no tool/emit activity for 5 min
    llm_call_timeout_seconds: int = 180  # per-call timeout for LLM acompletion()
    scope_timeout_seconds: int = 3600  # per sub-explorer scope timeout (1 hour)
    hatchet_execution_timeout_minutes: int = 180  # Hatchet workflow execution timeout (3 hours)
    hatchet_schedule_timeout_minutes: int = 60  # max queue wait before Hatchet cancels a task

    # Database pool (graph-db — read-optimized, mostly API reads)
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    # Write database (normalized, write-optimized — all worker pipeline writes)
    write_database_url: str = "postgresql+asyncpg://kt:localdev@localhost:5434/knowledge_tree_write"
    write_db_pool_size: int = 100
    write_db_max_overflow: int = 700
    write_db_pool_timeout: int = 120

    # Sync worker (write-db → graph-db)
    sync_interval_seconds: int = 5
    sync_batch_size: int = 1000
    sync_max_retries: int = 5
    sync_retry_base_seconds: int = 60

    log_level: str = "INFO"

    # Auth
    jwt_secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    skip_auth: bool = False  # set True in tests via SKIP_AUTH=true
    disable_self_registration: bool = False  # env override; when True, DB toggle is ignored

    # Google OAuth (empty string = disabled)
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""

    # MCP OAuth 2.1
    mcp_oauth_base_url: str = "http://localhost:8001"  # Public URL of MCP server

    # BYOK (Bring Your Own Key) — Fernet encryption key for stored API keys
    byok_encryption_key: str = ""

    # Email
    email_enabled: bool = False
    email_provider: str = "resend"
    email_verification: bool = False
    email_from_address: str = ""
    resend_api_key: str = ""

    model_config = {"extra": "ignore"}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Resolve paths at instantiation time so env vars can override in tests
        env_file = os.environ.get("KT_ENV_FILE", str(_PROJECT_ROOT / ".env"))
        yaml_path = os.environ.get("KT_CONFIG_FILE", _DEFAULT_YAML_PATH)
        dotenv_source = DotEnvSettingsSource(settings_cls, env_file=env_file)
        return (
            init_settings,
            env_settings,
            dotenv_source,
            YamlSettingsSource(settings_cls, yaml_path),
            file_secret_settings,
        )


def get_settings() -> Settings:
    return Settings()
