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

    from kt_plugins import bootstrap_worker_plugins, plugin_manager

    bootstrap_worker_plugins()

    from kt_hatchet.client import get_hatchet
    from kt_hatchet.lifespan import worker_lifespan
    from kt_worker_sync.workflows.dedup_pending_facts import dedup_pending_facts_wf
    from kt_worker_sync.workflows.sync import sync_dispatch_wf, sync_graph_wf

    hatchet = get_hatchet()
    _core_workflows = [sync_dispatch_wf, sync_graph_wf, dedup_pending_facts_wf]
    worker = hatchet.worker(
        "knowledge-tree-sync",
        # Connection budget: each graph sync uses pool_size=5, max_overflow=10
        # per DB. 10 slots × 15 connections × 2 DBs = up to 300 connections max.
        # Adjust if the PG max_connections budget is tight.
        slots=10,
        workflows=_core_workflows + plugin_manager.get_plugin_workflows(),
        lifespan=worker_lifespan,
    )
    logging.getLogger(__name__).info("Starting sync worker")
    worker.start()


if __name__ == "__main__":
    main()
