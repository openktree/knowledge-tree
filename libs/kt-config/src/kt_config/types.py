import uuid as _uuid
from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class FactType(str, Enum):
    claim = "claim"
    account = "account"
    measurement = "measurement"
    formula = "formula"
    quote = "quote"
    procedure = "procedure"
    reference = "reference"
    code = "code"
    image = "image"
    perspective = "perspective"


# Types that allow multi-sentence / structured content.
COMPOUND_FACT_TYPES: frozenset[str] = frozenset(
    {
        FactType.quote,
        FactType.procedure,
        FactType.reference,
        FactType.code,
        FactType.account,
    }
)


class NodeType(str, Enum):
    concept = "concept"  # Abstract topic, idea, technique, phenomenon
    perspective = "perspective"  # Debatable claim with stance-classified facts
    entity = "entity"  # Subject capable of intent (person, organization)
    event = "event"  # Temporal occurrence (historical, scientific, ongoing)
    synthesis = "synthesis"  # Composite node synthesising multiple source nodes
    supersynthesis = "supersynthesis"  # Meta-synthesis combining multiple synthesis nodes
    location = "location"  # Geographic place (country, city, region, landmark)


# Composite node types — built from other nodes rather than raw facts alone.
COMPOSITE_NODE_TYPES: frozenset[str] = frozenset({"synthesis", "supersynthesis", "perspective"})


class Visibility(str, Enum):
    public = "public"
    private = "private"


# Base (non-composite) node types — built directly from raw facts.
BASE_NODE_TYPES: frozenset[str] = frozenset({"concept", "entity", "event", "location"})


class EntitySubtype(str, Enum):
    person = "person"  # Individual human
    organization = "organization"  # Company, institution, government body, NGO
    other = "other"  # Unclassified entity


class FactStance(str, Enum):
    supports = "supports"
    challenges = "challenges"
    neutral = "neutral"


class EdgeType(str, Enum):
    related = "related"
    draws_from = "draws_from"  # Directed: composite → source node (programmatic, not LLM-created)


# Undirected edges — canonical UUID ordering enforced.
# NOTE: "draws_from" is intentionally excluded — it is a directed programmatic edge.
UNDIRECTED_EDGE_TYPES: frozenset[str] = frozenset({"related"})

# No structural edge types in the new model.
STRUCTURAL_EDGE_TYPES: frozenset[str] = frozenset()

# No transitive edge types in the new model.
TRANSITIVE_EDGE_TYPES: frozenset[str] = frozenset()


def canonicalize_edge_ids(source: UUID, target: UUID, relationship_type: str) -> tuple[UUID, UUID]:
    """Return (source, target) in canonical order for undirected edges.

    Undirected edges: the smaller UUID is always placed as source
    so that the unique constraint naturally prevents A->B / B->A duplicates.

    Directed edges (e.g. ``draws_from``): order is preserved as-is.
    """
    if relationship_type not in UNDIRECTED_EDGE_TYPES:
        return source, target
    if target < source:
        return target, source
    return source, target


# -- Default parent nodes (well-known deterministic UUIDs) ----------------

ALL_CONCEPTS_ID = _uuid.uuid5(_uuid.NAMESPACE_DNS, "knowledge-tree.all-concepts")
ALL_EVENTS_ID = _uuid.uuid5(_uuid.NAMESPACE_DNS, "knowledge-tree.all-events")
ALL_PERSPECTIVES_ID = _uuid.uuid5(_uuid.NAMESPACE_DNS, "knowledge-tree.all-perspectives")
ALL_LOCATIONS_ID = _uuid.uuid5(_uuid.NAMESPACE_DNS, "knowledge-tree.all-locations")

ALL_ENTITIES_ID = _uuid.uuid5(_uuid.NAMESPACE_DNS, "knowledge-tree.all-entities")
ALL_SYNTHESES_ID = _uuid.uuid5(_uuid.NAMESPACE_DNS, "knowledge-tree.all-syntheses")
ALL_SUPERSYNTHESES_ID = _uuid.uuid5(_uuid.NAMESPACE_DNS, "knowledge-tree.all-supersyntheses")

DEFAULT_PARENTS: dict[str, _uuid.UUID] = {
    "concept": ALL_CONCEPTS_ID,
    "entity": ALL_CONCEPTS_ID,  # base types all map to concepts
    "event": ALL_CONCEPTS_ID,
    "location": ALL_CONCEPTS_ID,
    "perspective": ALL_PERSPECTIVES_ID,
    "synthesis": ALL_SYNTHESES_ID,
    "supersynthesis": ALL_SUPERSYNTHESES_ID,
}


# Shared DTOs
class FactDTO(BaseModel):
    id: UUID
    content: str
    fact_type: FactType
    metadata: dict | None = None
    created_at: datetime


class NodeDTO(BaseModel):
    id: UUID
    concept: str
    node_type: NodeType = NodeType.concept
    parent_id: UUID | None = None
    attractor: str | None = None
    filter_id: str | None = None
    max_content_tokens: int = 500
    created_at: datetime
    updated_at: datetime
    update_count: int = 0
    access_count: int = 0


class EdgeDTO(BaseModel):
    id: UUID
    source_node_id: UUID
    target_node_id: UUID
    relationship_type: EdgeType
    weight: float
    justification: str | None = None
    created_at: datetime
    metadata: dict | None = None


class DimensionDTO(BaseModel):
    id: UUID
    node_id: UUID
    model_id: str
    content: str
    confidence: float
    suggested_concepts: list[str] = []
    generated_at: datetime


# Public re-export — canonical definition in kt_core_engine_api.search.types.
# Kept here permanently since many packages import from kt_config.types.
from kt_core_engine_api.search import RawSearchResult  # noqa: E402,F401
