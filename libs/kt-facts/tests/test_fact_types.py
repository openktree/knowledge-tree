"""Unit tests for fact type specifications."""

from kt_config.types import COMPOUND_FACT_TYPES, FactType
from kt_facts.prompt.types import (
    ALL_FACT_TYPES,
    FACT_TYPE_BY_NAME,
    IMAGE_FACT_TYPES,
    FactTypeSpec,
)


def test_all_fact_types_has_10_entries():
    assert len(ALL_FACT_TYPES) == 10


def test_all_fact_types_cover_all_enum_values():
    """Every FactType enum value has a corresponding FactTypeSpec."""
    enum_values = {ft.value for ft in FactType}
    spec_values = {spec.name for spec in ALL_FACT_TYPES}
    assert spec_values == enum_values


def test_fact_type_by_name_complete():
    """Lookup dict has all 10 entries and maps correctly."""
    assert len(FACT_TYPE_BY_NAME) == 10
    for spec in ALL_FACT_TYPES:
        assert FACT_TYPE_BY_NAME[spec.name] is spec


def test_is_compound_matches_shared_types():
    """is_compound property matches COMPOUND_FACT_TYPES from shared/types.py."""
    for spec in ALL_FACT_TYPES:
        expected = spec.fact_type.value in COMPOUND_FACT_TYPES
        assert spec.is_compound == expected, f"{spec.name}: expected is_compound={expected}, got {spec.is_compound}"


def test_image_fact_types_is_subset():
    """IMAGE_FACT_TYPES is a strict subset of ALL_FACT_TYPES."""
    all_names = {spec.name for spec in ALL_FACT_TYPES}
    image_names = {spec.name for spec in IMAGE_FACT_TYPES}
    assert image_names.issubset(all_names)
    assert len(IMAGE_FACT_TYPES) < len(ALL_FACT_TYPES)


def test_image_fact_types_expected_count():
    """IMAGE_FACT_TYPES has 7 entries (excludes account, formula, code)."""
    assert len(IMAGE_FACT_TYPES) == 7
    image_names = {spec.name for spec in IMAGE_FACT_TYPES}
    assert "account" not in image_names
    assert "formula" not in image_names
    assert "code" not in image_names


def test_fact_type_spec_name_property():
    """name property returns the enum value string."""
    spec = FactTypeSpec(
        fact_type=FactType.claim,
        description="test",
        length_rule="1-2 sentences",
    )
    assert spec.name == "claim"


def test_fact_type_spec_render():
    """render() produces a markdown bullet line."""
    spec = FactTypeSpec(
        fact_type=FactType.measurement,
        description="A quantitative data point",
        length_rule="1-2 sentences",
    )
    rendered = spec.render()
    assert rendered.startswith("- **measurement**")
    assert "A quantitative data point" in rendered
    assert "(1-2 sentences)" in rendered


def test_fact_type_spec_is_frozen():
    """FactTypeSpec is frozen (immutable)."""
    spec = ALL_FACT_TYPES[0]
    with __import__("pytest").raises(AttributeError):
        spec.description = "changed"  # type: ignore[misc]
