"""Tests for deterministic key generation."""

import uuid

from kt_db.keys import (
    KT_NAMESPACE,
    key_to_uuid,
    make_dimension_key,
    make_edge_key,
    make_node_key,
    make_source_key,
    uri_to_source_id,
)


def test_make_node_key_basic():
    assert make_node_key("Artificial Intelligence") == "artificial-intelligence"


def test_make_node_key_entity():
    assert make_node_key("OpenAI") == "openai"


def test_make_node_key_strips_whitespace():
    assert make_node_key("  quantum computing  ") == "quantum-computing"


def test_make_node_key_special_chars():
    assert make_node_key("2024 U.S. Election!") == "2024-u-s-election"


def test_make_node_key_truncation():
    long_concept = "a" * 300
    key = make_node_key(long_concept)
    assert len(key) <= 200


def test_make_edge_key_canonical_order():
    key1 = make_edge_key("related", "ai", "ml")
    key2 = make_edge_key("related", "ml", "ai")
    assert key1 == key2
    assert key1 == "related:ai--ml"


def test_make_edge_key_different_types_uses_related():
    key = make_edge_key("related", "openai", "ai")
    assert key == "related:ai--openai"


def test_make_dimension_key():
    key = make_dimension_key("ai", "grok-4.1-fast", 0)
    assert key == "ai:grok-4-1-fast:0"


def test_make_dimension_key_batch():
    key = make_dimension_key("ai", "grok-4.1-fast", 2)
    assert key == "ai:grok-4-1-fast:2"


# ── key_to_uuid tests ────────────────────────────────────────────────


def test_key_to_uuid_returns_uuid():
    result = key_to_uuid("artificial-intelligence")
    assert isinstance(result, uuid.UUID)


def test_key_to_uuid_deterministic():
    a = key_to_uuid("artificial-intelligence")
    b = key_to_uuid("artificial-intelligence")
    assert a == b


def test_key_to_uuid_different_keys_different_uuids():
    a = key_to_uuid("ai")
    b = key_to_uuid("ml")
    assert a != b


def test_key_to_uuid_is_uuid5():
    key = "test"
    result = key_to_uuid(key)
    expected = uuid.uuid5(KT_NAMESPACE, key)
    assert result == expected


def test_key_to_uuid_node_edge_different():
    """Node key and edge key with overlapping text produce different UUIDs."""
    node_uuid = key_to_uuid("ai")
    edge_uuid = key_to_uuid("related:ai--ml")
    assert node_uuid != edge_uuid


# ── source key tests ────────────────────────────────────────────────


def test_make_source_key():
    assert make_source_key("https://example.com/page") == "source:https://example.com/page"


def test_uri_to_source_id_returns_uuid():
    result = uri_to_source_id("https://example.com")
    assert isinstance(result, uuid.UUID)


def test_uri_to_source_id_deterministic():
    a = uri_to_source_id("https://example.com/page")
    b = uri_to_source_id("https://example.com/page")
    assert a == b


def test_uri_to_source_id_different_uris():
    a = uri_to_source_id("https://example.com/a")
    b = uri_to_source_id("https://example.com/b")
    assert a != b


def test_uri_to_source_id_matches_key_to_uuid():
    uri = "https://example.com/test"
    assert uri_to_source_id(uri) == key_to_uuid(make_source_key(uri))


def test_uri_to_source_id_different_from_node_uuid():
    """Source UUID should differ from node UUID even with similar text."""
    source_uuid = uri_to_source_id("ai")
    node_uuid = key_to_uuid("ai")
    assert source_uuid != node_uuid
