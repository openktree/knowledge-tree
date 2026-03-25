"""Node creation pipeline package.

Provides OOP classes for building, enriching, and connecting nodes in the
knowledge graph. Sub-pipelines handle nodes, edges, dimensions, and gathering.
"""

from kt_worker_nodes.pipelines.batch import BatchPipeline, NodePipeline
from kt_worker_nodes.pipelines.building.base import NodeBuilder
from kt_worker_nodes.pipelines.building.unified import UnifiedNodeBuilder
from kt_worker_nodes.pipelines.definitions.pipeline import DefinitionPipeline
from kt_worker_nodes.pipelines.dimensions.pipeline import DimensionPipeline
from kt_worker_nodes.pipelines.dimensions.types import DimensionResult
from kt_worker_nodes.pipelines.edges.classifier import (
    EDGE_RESOLUTION_SYSTEM_PROMPT,
    EdgeClassifier,
)
from kt_worker_nodes.pipelines.edges.pipeline import EdgePipeline
from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver
from kt_worker_nodes.pipelines.enrichment import PoolEnricher
from kt_worker_nodes.pipelines.gathering.pipeline import GatherFactsPipeline

# Backwards-compatible alias
FactGatherer = GatherFactsPipeline
from kt_worker_nodes.pipelines.gathering.types import GatherResult
from kt_worker_nodes.pipelines.models import (
    CreateNodeTask,
    EdgeCandidate,
    EdgeResolution,
    EnrichResult,
    NodeBuildResult,
    PerspectiveResult,
)
from kt_worker_nodes.pipelines.nodes.pipeline import NodeCreationPipeline

__all__ = [
    "BatchPipeline",
    "CreateNodeTask",
    "DefinitionPipeline",
    "DimensionPipeline",
    "DimensionResult",
    "EDGE_RESOLUTION_SYSTEM_PROMPT",
    "EdgeCandidate",
    "EdgeClassifier",
    "EdgePipeline",
    "EdgeResolution",
    "EdgeResolver",
    "EnrichResult",
    "FactGatherer",
    "GatherFactsPipeline",
    "GatherResult",
    "NodeBuilder",
    "NodeBuildResult",
    "NodeCreationPipeline",
    "NodePipeline",
    "PerspectiveResult",
    "PoolEnricher",
    "UnifiedNodeBuilder",
]
