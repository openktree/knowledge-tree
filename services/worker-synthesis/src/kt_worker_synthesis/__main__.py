"""Standalone worker for synthesis workflows.

Usage: python -m kt_worker_synthesis
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge Tree synthesis worker")
    parser.add_argument("--log-level", default="INFO", help="Log level (default: INFO)")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from kt_plugins import bootstrap_worker_plugins, plugin_manager

    bootstrap_worker_plugins()

    from kt_hatchet.client import get_hatchet
    from kt_hatchet.lifespan import worker_lifespan
    from kt_worker_synthesis.workflows.super_synthesizer import super_synthesizer_wf
    from kt_worker_synthesis.workflows.synthesizer import synthesizer_wf

    hatchet = get_hatchet()
    _core_workflows = [synthesizer_wf, super_synthesizer_wf]
    worker = hatchet.worker(
        "knowledge-tree-synthesis",
        slots=10,
        durable_slots=5,
        workflows=_core_workflows + plugin_manager.get_plugin_workflows(),
        lifespan=worker_lifespan,
    )
    logging.getLogger(__name__).info("Starting synthesis worker")
    worker.start()


if __name__ == "__main__":
    main()
