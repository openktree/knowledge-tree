"""Search worker entry point.

Usage: python -m kt_worker_search
"""

import logging

from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import worker_lifespan
from kt_worker_search.workflows.decompose import (
    decompose_source_task,
    decompose_sources_wf,
    entity_extraction_task,
    reingest_source_wf,
)
from kt_worker_search.workflows.search import (
    decompose_chunk_task,
    decompose_page_wf,
    search_wf,
)
from kt_worker_search.workflows.seed_dedup import seed_dedup_task


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
        "search",
        slots=50,
        workflows=[
            search_wf,
            decompose_page_wf,
            decompose_chunk_task,
            decompose_source_task,
            decompose_sources_wf,
            entity_extraction_task,
            seed_dedup_task,
            reingest_source_wf,
        ],
        lifespan=worker_lifespan,
    )
    worker.start()


if __name__ == "__main__":
    main()
