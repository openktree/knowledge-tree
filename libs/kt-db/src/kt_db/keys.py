"""Deterministic key generation for the write-optimized database.

Node keys:   {slug}
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


_SCHEMA_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_RESERVED_SCHEMAS = frozenset(
    {
        "public",
        "pg_catalog",
        "pg_toast",
        "pg_temp",
        "information_schema",
    }
)


def validate_schema_name(schema_name: str) -> None:
    """Validate a PostgreSQL schema name for use in DDL/search_path.

    Raises ValueError if the name contains characters outside [a-z0-9_]
    or matches a reserved PostgreSQL schema name.
    This is the single source of truth for schema name validation — all
    code paths that use schema names in SQL must call this.
    """
    if not _SCHEMA_NAME_RE.match(schema_name):
        raise ValueError(f"Invalid schema name '{schema_name}': must match ^[a-z0-9_]+$")
    if schema_name in _RESERVED_SCHEMAS or schema_name.startswith("pg_"):
        raise ValueError(f"Reserved schema name: '{schema_name}'")


def key_to_uuid(write_key: str) -> uuid.UUID:
    """Derive a deterministic UUID from a write-db key.

    Examples:
        key_to_uuid("artificial-intelligence") -> UUID(...)
        key_to_uuid("related:ai--ml") -> UUID(...)

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


def make_node_key(concept: str) -> str:
    """Generate a deterministic node key from concept name.

    Examples:
        make_node_key("Artificial Intelligence") -> "artificial-intelligence"
        make_node_key("OpenAI") -> "openai"
    """
    return _slugify(concept)


# Legacy type prefixes — used only by url_key_to_node_key to parse
# old-format keys (e.g. "concept-artificial-intelligence") during migration.
_KNOWN_NODE_TYPES = ("concept", "entity", "perspective", "event", "synthesis", "supersynthesis", "location")


def node_key_to_url_key(node_key: str) -> str:
    """Convert a node key to a URL-friendly format.

    With the new typeless keys, this is an identity function.
    Kept for API compatibility.
    """
    return node_key


def url_key_to_node_key(url_key: str) -> str:
    """Convert a URL-friendly key back to a canonical node key.

    Handles both old-format keys (with type prefix) and new plain slugs.
    Old format: "concept-artificial-intelligence" -> "artificial-intelligence"
    New format: "artificial-intelligence" -> "artificial-intelligence"
    """
    # Strip legacy type prefix if present
    for nt in _KNOWN_NODE_TYPES:
        prefix = f"{nt}-"
        if url_key.startswith(prefix):
            return url_key[len(prefix) :]
    return url_key


def make_url_key(concept: str) -> str:
    """Generate a URL-friendly node key from concept name.

    Examples:
        make_url_key("Artificial Intelligence") -> "artificial-intelligence"
    """
    return make_node_key(concept)


def make_edge_key(rel_type: str, node_key_a: str, node_key_b: str) -> str:
    """Generate a deterministic edge key.

    For undirected edges: canonical alphabetical ordering prevents duplicates.
    For directed edges (e.g. ``draws_from``): order is preserved.

    Examples:
        make_edge_key("related", "ai", "ml")
        -> "related:ai--ml"
        make_edge_key("draws_from", "synthesis-X", "Y")
        -> "draws_from:synthesis-X--Y"  (order preserved)
    """
    from kt_config.types import UNDIRECTED_EDGE_TYPES

    if rel_type in UNDIRECTED_EDGE_TYPES:
        a, b = sorted([node_key_a, node_key_b])
        return f"{rel_type}:{a}--{b}"
    return f"{rel_type}:{node_key_a}--{node_key_b}"


def make_seed_key(name: str) -> str:
    """Generate a deterministic seed key from name.

    Produces the same key as make_node_key() for the same name,
    enabling seamless seed-to-node promotion.
    """
    return _slugify(name)


def make_source_key(uri: str) -> str:
    """Deterministic source key from URI."""
    return f"source:{uri}"


def uri_to_source_id(uri: str) -> uuid.UUID:
    """Derive a deterministic source UUID from a URI.

    Same URI always produces the same UUID, ensuring write-db and graph-db
    agree on source IDs without coordination.
    """
    return key_to_uuid(make_source_key(uri))


def make_dimension_key(node_key: str, model_id: str, batch_index: int = 0) -> str:
    """Generate a deterministic dimension key.

    Examples:
        make_dimension_key("ai", "grok-4-1-fast", 0)
        -> "ai:grok-4-1-fast:0"
    """
    return f"{node_key}:{_slugify(model_id)}:{batch_index}"
