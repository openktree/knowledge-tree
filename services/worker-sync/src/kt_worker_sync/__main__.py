"""Sync worker: write-db → graph-db incremental synchronization.

Usage: python -m kt_worker_sync
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge Tree sync worker")
    parser.add_argument("--log-level", default="INFO", help="Log level (default: INFO)")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    from kt_hatchet.client import get_hatchet
    from kt_hatchet.lifespan import worker_lifespan
    from kt_worker_sync.workflows.sync import sync_dispatch_wf, sync_graph_wf

    hatchet = get_hatchet()
    worker = hatchet.worker(
        "knowledge-tree-sync",
        slots=10,  # Allow parallel per-graph sync tasks
        workflows=[sync_dispatch_wf, sync_graph_wf],
        lifespan=worker_lifespan,
    )
    logging.getLogger(__name__).info("Starting sync worker")
    worker.start()


if __name__ == "__main__":
    main()
