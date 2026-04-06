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
    from kt_worker_nodes.workflows.node_pipeline import (
        edge_task,
        node_pipeline_wf,
    )

    hatchet = get_hatchet()
    worker = hatchet.worker(
        "worker-nodes",
        lifespan=worker_lifespan,
        workflows=[
            node_pipeline_wf,
            edge_task,
            auto_build_task,
        ],
    )
    logging.getLogger(__name__).info("Starting worker-nodes")
    worker.start()


if __name__ == "__main__":
    main()
