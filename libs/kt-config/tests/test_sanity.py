from datetime import UTC, datetime
from uuid import uuid4


def test_import():
    import kt_config

    assert kt_config is not None


def test_settings_load():
    from kt_config.settings import get_settings

    settings = get_settings()
    assert settings.database_url is not None
    assert "postgresql" in settings.database_url


def test_fact_types_complete():
    from kt_config.types import FactType

    expected = {
        "claim",
        "account",
        "measurement",
        "formula",
        "quote",
        "procedure",
        "reference",
        "code",
        "image",
        "perspective",
    }
    assert set(ft.value for ft in FactType) == expected


def test_edge_types_complete():
    from kt_config.types import EdgeType

    expected = {"related", "cross_type", "draws_from"}
    assert set(et.value for et in EdgeType) == expected


def test_node_types_complete():
    from kt_config.types import NodeType

    expected = {"concept", "perspective", "entity", "event", "synthesis", "location"}
    assert set(nt.value for nt in NodeType) == expected


def test_fact_stance_complete():
    from kt_config.types import FactStance

    expected = {"supports", "challenges", "neutral"}
    assert set(fs.value for fs in FactStance) == expected


def test_pydantic_dto_serialization():
    from kt_config.types import FactDTO, FactType

    fact = FactDTO(
        id=uuid4(),
        content="Water boils at 100\u00b0C",
        fact_type=FactType.measurement,
        created_at=datetime.now(UTC),
    )
    data = fact.model_dump()
    assert data["fact_type"] == "measurement"
    roundtrip = FactDTO.model_validate(data)
    assert roundtrip.content == fact.content


def test_settings_edge_staleness_days_default():
    from kt_config.settings import get_settings

    settings = get_settings()
    assert settings.edge_staleness_days == 30


def test_errors():
    from kt_config.errors import (
        BudgetExhaustedError,
        DuplicateNodeError,
        EmbeddingError,
        ModelError,
        NodeNotFoundError,
        ProviderError,
    )

    e = NodeNotFoundError("abc-123")
    assert "abc-123" in str(e)
    e = BudgetExhaustedError("nav")
    assert "nav" in str(e)
    e = ProviderError("brave", "timeout")
    assert "brave" in str(e)
    e = ModelError("gpt-4", "rate limit")
    assert "gpt-4" in str(e)
    e = DuplicateNodeError("quantum computing")
    assert "quantum computing" in str(e)
    e = EmbeddingError("dimension mismatch")
    assert "dimension mismatch" in str(e)
