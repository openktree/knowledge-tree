"""Ingest worker entry point.

Usage: python -m kt_worker_ingest
"""

import logging

from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import worker_lifespan
from kt_worker_ingest.workflows.ingest import (
    ingest_build_wf,
    ingest_confirm_wf,
    ingest_decompose_wf,
    ingest_partition_wf,
)
from kt_worker_ingest.workflows.public_cache_sweeper import public_cache_sweep_wf


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Register plugins that extend the decomposition pipeline.
    from kt_facts.register_plugins import register_kt_facts_plugins

    register_kt_facts_plugins()

    hatchet = get_hatchet()
    worker = hatchet.worker(
        "ingest",
        slots=10,
        workflows=[
            ingest_build_wf,
            ingest_confirm_wf,
            ingest_decompose_wf,
            ingest_partition_wf,
            public_cache_sweep_wf,
        ],
        lifespan=worker_lifespan,
    )
    worker.start()


if __name__ == "__main__":
    main()
