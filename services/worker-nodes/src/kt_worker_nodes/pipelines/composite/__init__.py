"""Composite node pipelines — synthesis and perspective agents."""

from kt_worker_nodes.pipelines.composite.merge import find_mergeable_composite
from kt_worker_nodes.pipelines.composite.perspective_agent import build_perspective_impl
from kt_worker_nodes.pipelines.composite.synthesis_agent import build_synthesis_impl

__all__ = [
    "build_perspective_impl",
    "build_synthesis_impl",
    "find_mergeable_composite",
]
