"""Unit tests for fact rejection tracking in config and models."""

from kt_config.settings import get_settings
from kt_db.models import DimensionFact, NodeFactRejection


def test_settings_dimension_defaults():
    """Verify dimension batching settings have correct defaults."""
    settings = get_settings()
    assert settings.dimension_fact_limit == 60
    assert settings.dimension_saturation_ratio == 0.7
    assert settings.dimension_pool_multiplier == 2
    assert settings.definition_model == ""
    assert settings.definition_thinking_level == ""


def test_node_fact_rejection_model():
    """Verify NodeFactRejection model has expected tablename and constraints."""
    assert NodeFactRejection.__tablename__ == "node_fact_rejections"
    # Check unique constraint exists
    constraint_names = [c.name for c in NodeFactRejection.__table_args__ if hasattr(c, "name")]
    assert "uq_node_fact_rejection" in constraint_names


def test_dimension_fact_model():
    """Verify DimensionFact model has expected tablename and constraints."""
    assert DimensionFact.__tablename__ == "dimension_facts"
    constraint_names = [c.name for c in DimensionFact.__table_args__ if hasattr(c, "name")]
    assert "uq_dimension_fact" in constraint_names


def test_dimension_model_new_fields():
    """Verify Dimension model has the new batching fields."""
    from kt_db.models import Dimension

    # Check columns exist in the model's table
    col_names = {c.name for c in Dimension.__table__.columns}
    assert "batch_index" in col_names
    assert "fact_count" in col_names
    assert "is_definitive" in col_names


def test_node_model_definition_fields():
    """Verify Node model has the new definition fields."""
    from kt_db.models import Node

    col_names = {c.name for c in Node.__table__.columns}
    assert "definition" in col_names
    assert "definition_generated_at" in col_names


def test_saturation_threshold_calculation():
    """Verify saturation threshold math: 0.7 * 60 = 42."""
    settings = get_settings()
    threshold = int(settings.dimension_fact_limit * settings.dimension_saturation_ratio)
    assert threshold == 42


def test_pool_search_limit_calculation():
    """Verify pool search limit: 2 * 60 = 120."""
    settings = get_settings()
    pool_limit = settings.dimension_fact_limit * settings.dimension_pool_multiplier
    assert pool_limit == 120
