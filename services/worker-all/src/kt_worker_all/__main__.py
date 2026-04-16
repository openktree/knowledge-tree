"""All-in-one worker for local development.

Registers all workflows from all worker packages on a single Hatchet worker.

Usage: python -m kt_worker_all
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge Tree all-in-one Hatchet worker")
    parser.add_argument("--log-level", default="INFO", help="Log level (default: INFO)")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    # Suppress noisy third-party loggers
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM Router").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM Proxy").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    try:
        import litellm

        litellm.suppress_debug_info = True
    except Exception:
        pass

    # LiteLLM's aembedding/acompletion use loop.run_in_executor(None, ...)
    # which shares the default thread pool. Increase it so LLM calls don't
    # queue behind each other and starve Hatchet's task scheduling.
    import asyncio
    import concurrent.futures

    asyncio.get_event_loop().set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=64))

    from kt_config.plugin import load_default_plugins
    from kt_providers.registry import bridge_plugin_search_providers

    load_default_plugins()
    bridge_plugin_search_providers()

    from kt_hatchet.client import get_hatchet
    from kt_hatchet.lifespan import worker_lifespan
    from kt_worker_bottomup.bottom_up import (
        agent_select_wf,
        bottom_up_prepare_scope_wf,
        bottom_up_prepare_wf,
        bottom_up_scope_wf,
        bottom_up_wf,
    )

    # Import all workflows from all worker packages
    from kt_worker_ingest.workflows.ingest import (
        ingest_build_wf,
        ingest_confirm_wf,
        ingest_decompose_wf,
        ingest_partition_wf,
    )
    from kt_worker_ingest.workflows.orphan_fact_sweeper import orphan_fact_sweep_wf
    from kt_worker_ingest.workflows.public_cache_sweeper import public_cache_sweep_wf
    from kt_worker_nodes.workflows.auto_build import auto_build_task
    from kt_worker_nodes.workflows.composite import (
        build_composite_task,
        regenerate_composite_task,
    )
    from kt_worker_nodes.workflows.node_pipeline import (
        edge_task,
        node_pipeline_wf,
    )
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
    from kt_worker_sync.workflows.dedup_pending_facts import dedup_pending_facts_wf
    from kt_worker_sync.workflows.sync import sync_dispatch_wf, sync_graph_wf
    from kt_worker_synthesis.workflows.super_synthesizer import super_synthesizer_wf
    from kt_worker_synthesis.workflows.synthesizer import synthesizer_wf

    hatchet = get_hatchet()
    worker = hatchet.worker(
        "knowledge-tree-all",
        slots=500,
        durable_slots=250,
        workflows=[
            agent_select_wf,
            bottom_up_wf,
            bottom_up_scope_wf,
            bottom_up_prepare_scope_wf,
            bottom_up_prepare_wf,
            search_wf,
            decompose_page_wf,
            decompose_chunk_task,
            decompose_source_task,
            decompose_sources_wf,
            entity_extraction_task,
            seed_dedup_task,
            reingest_source_wf,
            node_pipeline_wf,
            edge_task,
            auto_build_task,
            build_composite_task,
            regenerate_composite_task,
            ingest_build_wf,
            ingest_confirm_wf,
            ingest_decompose_wf,
            ingest_partition_wf,
            public_cache_sweep_wf,
            orphan_fact_sweep_wf,
            sync_dispatch_wf,
            sync_graph_wf,
            dedup_pending_facts_wf,
            synthesizer_wf,
            super_synthesizer_wf,
        ],
        lifespan=worker_lifespan,
    )
    logging.getLogger(__name__).info("Starting all-in-one worker")
    worker.start()


if __name__ == "__main__":
    main()
