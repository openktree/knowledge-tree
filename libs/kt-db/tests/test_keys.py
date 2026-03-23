"""Tests for deterministic key generation."""

import uuid

from kt_db.keys import KT_NAMESPACE, key_to_uuid, make_dimension_key, make_edge_key, make_node_key


def test_make_node_key_basic():
    assert make_node_key("concept", "Artificial Intelligence") == "concept:artificial-intelligence"


def test_make_node_key_entity():
    assert make_node_key("entity", "OpenAI") == "entity:openai"


def test_make_node_key_strips_whitespace():
    assert make_node_key("concept", "  quantum computing  ") == "concept:quantum-computing"


def test_make_node_key_special_chars():
    assert make_node_key("event", "2024 U.S. Election!") == "event:2024-u-s-election"


def test_make_node_key_truncation():
    long_concept = "a" * 300
    key = make_node_key("concept", long_concept)
    # "concept:" + slug
    slug = key.split(":", 1)[1]
    assert len(slug) <= 200


def test_make_edge_key_canonical_order():
    key1 = make_edge_key("related", "concept:ai", "concept:ml")
    key2 = make_edge_key("related", "concept:ml", "concept:ai")
    assert key1 == key2
    assert key1 == "related:concept:ai--concept:ml"


def test_make_edge_key_cross_type():
    key = make_edge_key("cross_type", "entity:openai", "concept:ai")
    assert key == "cross_type:concept:ai--entity:openai"


def test_make_dimension_key():
    key = make_dimension_key("concept:ai", "grok-4.1-fast", 0)
    assert key == "concept:ai:grok-4-1-fast:0"


def test_make_dimension_key_batch():
    key = make_dimension_key("concept:ai", "grok-4.1-fast", 2)
    assert key == "concept:ai:grok-4-1-fast:2"


# ── key_to_uuid tests ────────────────────────────────────────────────


def test_key_to_uuid_returns_uuid():
    result = key_to_uuid("concept:artificial-intelligence")
    assert isinstance(result, uuid.UUID)


def test_key_to_uuid_deterministic():
    a = key_to_uuid("concept:artificial-intelligence")
    b = key_to_uuid("concept:artificial-intelligence")
    assert a == b


def test_key_to_uuid_different_keys_different_uuids():
    a = key_to_uuid("concept:ai")
    b = key_to_uuid("concept:ml")
    assert a != b


def test_key_to_uuid_is_uuid5():
    key = "concept:test"
    result = key_to_uuid(key)
    expected = uuid.uuid5(KT_NAMESPACE, key)
    assert result == expected


def test_key_to_uuid_node_edge_different():
    """Node key and edge key with overlapping text produce different UUIDs."""
    node_uuid = key_to_uuid("concept:ai")
    edge_uuid = key_to_uuid("related:concept:ai--concept:ml")
    assert node_uuid != edge_uuid
