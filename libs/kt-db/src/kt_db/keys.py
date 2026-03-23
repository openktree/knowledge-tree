"""Deterministic key generation for the write-optimized database.

Node keys:   {node_type}:{slug}
Edge keys:   {rel_type}:{node_key_a}--{node_key_b}  (alphabetically sorted)
Dimension keys: {node_key}:{model_slug}:{batch_index}

``key_to_uuid`` derives a deterministic UUID5 from any write key so that both
the write-db (TEXT PK) and the graph-db (UUID PK) share the same identity.
"""

import re
import uuid

# Fixed namespace for deterministic UUID generation.
# uuid5(KT_NAMESPACE, write_key) → same UUID every time for a given key.
KT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "knowledge-tree")


def key_to_uuid(write_key: str) -> uuid.UUID:
    """Derive a deterministic UUID from a write-db key.

    Examples:
        key_to_uuid("concept:artificial-intelligence") -> UUID(...)
        key_to_uuid("related:concept:ai--concept:ml") -> UUID(...)

    The same key always produces the same UUID.
    """
    return uuid.uuid5(KT_NAMESPACE, write_key)


def _slugify(text: str, max_length: int = 200) -> str:
    """Normalize text into a URL-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug


def make_node_key(node_type: str, concept: str) -> str:
    """Generate a deterministic node key from type and concept name.

    Examples:
        make_node_key("concept", "Artificial Intelligence") -> "concept:artificial-intelligence"
        make_node_key("entity", "OpenAI") -> "entity:openai"
    """
    return f"{node_type}:{_slugify(concept)}"


_KNOWN_NODE_TYPES = ("concept", "entity", "perspective", "event", "synthesis", "location")


def node_key_to_url_key(node_key: str) -> str:
    """Convert a node key to a URL-friendly format.

    Replaces the ``type:slug`` colon separator with a dash.

    Examples:
        node_key_to_url_key("concept:artificial-intelligence")
        -> "concept-artificial-intelligence"
        node_key_to_url_key("entity:openai") -> "entity-openai"
    """
    return node_key.replace(":", "-", 1)


def url_key_to_node_key(url_key: str) -> str:
    """Convert a URL-friendly key back to a canonical node key.

    Identifies the known node-type prefix and restores the colon separator.

    Examples:
        url_key_to_node_key("concept-artificial-intelligence")
        -> "concept:artificial-intelligence"
        url_key_to_node_key("entity-openai") -> "entity:openai"
    """
    for nt in _KNOWN_NODE_TYPES:
        prefix = f"{nt}-"
        if url_key.startswith(prefix):
            return f"{nt}:{url_key[len(prefix) :]}"
    # Fallback: return as-is (caller will try key_to_uuid which may fail)
    return url_key


def make_url_key(node_type: str, concept: str) -> str:
    """Generate a URL-friendly node key from type and concept name.

    Examples:
        make_url_key("concept", "Artificial Intelligence")
        -> "concept-artificial-intelligence"
    """
    return node_key_to_url_key(make_node_key(node_type, concept))


def make_edge_key(rel_type: str, node_key_a: str, node_key_b: str) -> str:
    """Generate a deterministic edge key.

    For undirected edges: canonical alphabetical ordering prevents duplicates.
    For directed edges (e.g. ``draws_from``): order is preserved.

    Examples:
        make_edge_key("related", "concept:ai", "concept:ml")
        -> "related:concept:ai--concept:ml"
        make_edge_key("draws_from", "synthesis:X", "concept:Y")
        -> "draws_from:synthesis:X--concept:Y"  (order preserved)
    """
    from kt_config.types import UNDIRECTED_EDGE_TYPES

    if rel_type in UNDIRECTED_EDGE_TYPES:
        a, b = sorted([node_key_a, node_key_b])
        return f"{rel_type}:{a}--{b}"
    return f"{rel_type}:{node_key_a}--{node_key_b}"


def make_seed_key(node_type: str, name: str) -> str:
    """Generate a deterministic seed key from type and name.

    Produces the same key as make_node_key() for the same type+name,
    enabling seamless seed-to-node promotion.
    """
    return f"{node_type}:{_slugify(name)}"


def make_dimension_key(node_key: str, model_id: str, batch_index: int = 0) -> str:
    """Generate a deterministic dimension key.

    Examples:
        make_dimension_key("concept:ai", "grok-4.1-fast", 0)
        -> "concept:ai:grok-4-1-fast:0"
    """
    return f"{node_key}:{_slugify(model_id)}:{batch_index}"
