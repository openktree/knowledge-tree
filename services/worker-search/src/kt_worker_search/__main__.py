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

    from kt_plugins import bootstrap_worker_plugins, plugin_manager

    bootstrap_worker_plugins()

    hatchet = get_hatchet()
    _core_workflows = [
        search_wf,
        decompose_page_wf,
        decompose_chunk_task,
        decompose_source_task,
        decompose_sources_wf,
        entity_extraction_task,
        seed_dedup_task,
        reingest_source_wf,
    ]
    worker = hatchet.worker(
        "search",
        slots=50,
        workflows=_core_workflows + plugin_manager.get_plugin_workflows(),
        lifespan=worker_lifespan,
    )
    worker.start()


if __name__ == "__main__":
    main()
