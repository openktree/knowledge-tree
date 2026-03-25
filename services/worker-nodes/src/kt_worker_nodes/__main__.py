"""Worker-nodes service entry point."""

from __future__ import annotations

import logging


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    from kt_hatchet.client import get_hatchet
    from kt_hatchet.lifespan import worker_lifespan
    from kt_worker_nodes.workflows.auto_build import auto_build_task
    from kt_worker_nodes.workflows.enrich_node import enrich_edge_task
    from kt_worker_nodes.workflows.node_pipeline import (
        crystallize_task,
        edge_task,
        node_pipeline_wf,
    )
    from kt_worker_nodes.workflows.rebuild_node import rebuild_node_task

    hatchet = get_hatchet()
    worker = hatchet.worker(
        "worker-nodes",
        lifespan=worker_lifespan,
        workflows=[
            node_pipeline_wf,
            edge_task,
            crystallize_task,
            rebuild_node_task,
            auto_build_task,
            enrich_edge_task,
        ],
    )
    logging.getLogger(__name__).info("Starting worker-nodes")
    worker.start()


if __name__ == "__main__":
    main()
