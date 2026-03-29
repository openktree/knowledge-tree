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

    # Discover plugin-contributed workflows
    from kt_config.settings import get_settings
    from kt_hatchet.client import get_hatchet
    from kt_hatchet.lifespan import worker_lifespan
    from kt_plugins.manager import PluginManager
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
    from kt_worker_nodes.workflows.auto_build import auto_build_task
    from kt_worker_nodes.workflows.composite import (
        build_composite_task,
        regenerate_composite_task,
    )
    from kt_worker_nodes.workflows.enrich_node import enrich_edge_task
    from kt_worker_nodes.workflows.node_pipeline import (
        crystallize_task,
        edge_task,
        node_pipeline_wf,
    )
    from kt_worker_nodes.workflows.rebuild_node import rebuild_node_task
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
    from kt_worker_sync.workflows.sync import sync_wf
    from kt_worker_synthesis.workflows.super_synthesizer import super_synthesizer_wf
    from kt_worker_synthesis.workflows.synthesizer import synthesizer_wf

    plugin_workflows: list[object] = []
    try:
        import asyncio

        settings = get_settings()
        plugin_manager = PluginManager()
        asyncio.get_event_loop().run_until_complete(
            plugin_manager.initialize(
                enabled_plugins=settings.enabled_plugins or None,
                license_keys=settings.plugin_license_keys,
            )
        )
        plugin_workflows = plugin_manager.get_plugin_workflows()
        if plugin_workflows:
            logging.getLogger(__name__).info("Loaded %d plugin workflow(s)", len(plugin_workflows))
    except Exception:
        logging.getLogger(__name__).warning("Plugin workflow discovery failed", exc_info=True)

    hatchet = get_hatchet()
    worker = hatchet.worker(
        "knowledge-tree-all",
        slots=100,
        durable_slots=50,
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
            crystallize_task,
            rebuild_node_task,
            auto_build_task,
            enrich_edge_task,
            build_composite_task,
            regenerate_composite_task,
            ingest_build_wf,
            ingest_confirm_wf,
            ingest_decompose_wf,
            ingest_partition_wf,
            sync_wf,
            synthesizer_wf,
            super_synthesizer_wf,
            *plugin_workflows,
        ],
        lifespan=worker_lifespan,
    )
    logging.getLogger(__name__).info("Starting all-in-one worker")
    worker.start()


if __name__ == "__main__":
    main()
