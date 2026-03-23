"""Fact gathering sub-pipeline: external search + decomposition."""

from kt_worker_nodes.pipelines.gathering.pipeline import GatherFactsPipeline

# Backwards-compatible alias
FactGatherer = GatherFactsPipeline

__all__ = ["FactGatherer", "GatherFactsPipeline"]
