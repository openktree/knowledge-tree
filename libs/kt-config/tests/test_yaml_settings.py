def _isolate_config(monkeypatch, tmp_path, yaml_content: str = "") -> None:
    """Point both YAML and .env to tmp_path so real files don't interfere."""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(yaml_content)
    monkeypatch.setenv("KT_CONFIG_FILE", str(yaml_file))
    monkeypatch.setenv("KT_ENV_FILE", str(tmp_path / "empty.env"))


def test_yaml_section_overrides_defaults(tmp_path, monkeypatch):
    """YAML values in sections should override Python defaults."""
    _isolate_config(
        monkeypatch,
        tmp_path,
        "orchestrator:\n  nav_budget: 999\ninfrastructure:\n  log_level: DEBUG\n",
    )
    monkeypatch.delenv("DEFAULT_NAV_BUDGET", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    from kt_config.settings import Settings

    s = Settings()
    assert s.default_nav_budget == 999
    assert s.log_level == "DEBUG"


def test_env_overrides_yaml(tmp_path, monkeypatch):
    """Environment variables should take precedence over YAML."""
    _isolate_config(
        monkeypatch,
        tmp_path,
        "orchestrator:\n  nav_budget: 999\n",
    )
    monkeypatch.setenv("DEFAULT_NAV_BUDGET", "42")

    from kt_config.settings import Settings

    s = Settings()
    assert s.default_nav_budget == 42


def test_missing_yaml_file_uses_defaults(tmp_path, monkeypatch):
    """Missing YAML file should gracefully fall back to defaults."""
    monkeypatch.setenv("KT_CONFIG_FILE", str(tmp_path / "nonexistent.yaml"))
    monkeypatch.setenv("KT_ENV_FILE", str(tmp_path / "empty.env"))
    monkeypatch.delenv("DEFAULT_NAV_BUDGET", raising=False)

    from kt_config.settings import Settings

    s = Settings()
    assert s.default_nav_budget == 200


def test_empty_yaml_file_uses_defaults(tmp_path, monkeypatch):
    """Empty YAML file should gracefully fall back to defaults."""
    _isolate_config(monkeypatch, tmp_path, "")
    monkeypatch.delenv("DEFAULT_NAV_BUDGET", raising=False)

    from kt_config.settings import Settings

    s = Settings()
    assert s.default_nav_budget == 200


def test_yaml_complex_types_in_section(tmp_path, monkeypatch):
    """YAML should handle complex types like dicts within sections."""
    _isolate_config(
        monkeypatch,
        tmp_path,
        'seeds:\n  re_embed_thresholds: "5,15,50"\n',
    )

    from kt_config.settings import Settings

    s = Settings()
    assert s.seed_re_embed_thresholds == "5,15,50"


def test_yaml_bool_types(tmp_path, monkeypatch):
    """YAML booleans should map correctly."""
    _isolate_config(
        monkeypatch,
        tmp_path,
        "search:\n  enable_full_text_fetch: false\norchestrator:\n  enable_semantic_expansion: true\n",
    )
    monkeypatch.delenv("ENABLE_FULL_TEXT_FETCH", raising=False)
    monkeypatch.delenv("ENABLE_SEMANTIC_EXPANSION", raising=False)

    from kt_config.settings import Settings

    s = Settings()
    assert s.enable_full_text_fetch is False
    assert s.enable_semantic_expansion is True


def test_yaml_unknown_section_ignored(tmp_path, monkeypatch):
    """Unknown sections and keys should be silently ignored."""
    _isolate_config(
        monkeypatch,
        tmp_path,
        "unknown_section:\n  totally_unknown_key: 123\ninfrastructure:\n  log_level: WARNING\n",
    )
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    from kt_config.settings import Settings

    s = Settings()
    assert s.log_level == "WARNING"
    assert not hasattr(s, "totally_unknown_key")


def test_same_key_different_sections_no_collision(tmp_path, monkeypatch):
    """'model' in orchestrator vs decomposition should map to different fields."""
    _isolate_config(
        monkeypatch,
        tmp_path,
        "orchestrator:\n"
        "  model: orch/model-a\n"
        "decomposition:\n"
        "  model: decomp/model-b\n"
        "ontology:\n"
        "  model: onto/model-c\n",
    )
    monkeypatch.delenv("ORCHESTRATOR_MODEL", raising=False)
    monkeypatch.delenv("DECOMPOSITION_MODEL", raising=False)
    monkeypatch.delenv("ONTOLOGY_MODEL", raising=False)

    from kt_config.settings import Settings

    s = Settings()
    assert s.orchestrator_model == "orch/model-a"
    assert s.decomposition_model == "decomp/model-b"
    assert s.ontology_model == "onto/model-c"


def test_short_keys_map_correctly(tmp_path, monkeypatch):
    """Short YAML keys should map to their full Settings field names."""
    _isolate_config(
        monkeypatch,
        tmp_path,
        "embeddings:\n"
        "  dimensions: 1536\n"
        "  timeout: 60\n"
        "edges:\n"
        "  staleness_days: 14\n"
        "ingest:\n"
        "  upload_dir: /tmp/uploads\n",
    )
    monkeypatch.delenv("EMBEDDING_DIMENSIONS", raising=False)
    monkeypatch.delenv("EMBEDDING_TIMEOUT", raising=False)
    monkeypatch.delenv("EDGE_STALENESS_DAYS", raising=False)
    monkeypatch.delenv("INGEST_UPLOAD_DIR", raising=False)

    from kt_config.settings import Settings

    s = Settings()
    assert s.embedding_dimensions == 1536
    assert s.embedding_timeout == 60
    assert s.edge_staleness_days == 14
    assert s.ingest_upload_dir == "/tmp/uploads"


def test_multiple_fields_per_section(tmp_path, monkeypatch):
    """Multiple fields within a single section should all be picked up."""
    _isolate_config(
        monkeypatch,
        tmp_path,
        "decomposition:\n"
        "  model: test/model\n"
        "  thinking_level: high\n"
        "  fact_pool_threshold: 0.8\n"
        "  max_content_tokens: 1000\n",
    )
    monkeypatch.delenv("DECOMPOSITION_MODEL", raising=False)
    monkeypatch.delenv("DECOMPOSITION_THINKING_LEVEL", raising=False)
    monkeypatch.delenv("FACT_POOL_THRESHOLD", raising=False)
    monkeypatch.delenv("DEFAULT_MAX_CONTENT_TOKENS", raising=False)

    from kt_config.settings import Settings

    s = Settings()
    assert s.decomposition_model == "test/model"
    assert s.decomposition_thinking_level == "high"
    assert s.fact_pool_threshold == 0.8
    assert s.default_max_content_tokens == 1000


def test_fetch_section_maps_to_settings(tmp_path, monkeypatch):
    """fetch: section should populate Settings.crossref_email / fetch_flaresolverr_url etc."""
    _isolate_config(
        monkeypatch,
        tmp_path,
        "fetch:\n"
        "  crossref_email: ops@example.com\n"
        "  unpaywall_email: ops@example.com\n"
        "  flaresolverr_url: http://byparr:8191/v1\n"
        "  flaresolverr_timeout: 90.0\n"
        "  provider_chain: doi,httpx\n"
        "  curl_cffi_impersonate: chrome131\n",
    )
    for var in (
        "CROSSREF_EMAIL",
        "UNPAYWALL_EMAIL",
        "FETCH_FLARESOLVERR_URL",
        "FETCH_FLARESOLVERR_TIMEOUT",
        "FETCH_PROVIDER_CHAIN",
        "FETCH_CURL_CFFI_IMPERSONATE",
    ):
        monkeypatch.delenv(var, raising=False)

    from kt_config.settings import Settings

    s = Settings()
    assert s.crossref_email == "ops@example.com"
    assert s.unpaywall_email == "ops@example.com"
    assert s.fetch_flaresolverr_url == "http://byparr:8191/v1"
    assert s.fetch_flaresolverr_timeout == 90.0
    assert s.fetch_provider_chain == "doi,httpx"
    assert s.fetch_curl_cffi_impersonate == "chrome131"


def test_project_config_yaml_loads():
    """The project root config.yaml should load without errors."""
    from kt_config.settings import Settings

    s = Settings()
    assert s is not None
