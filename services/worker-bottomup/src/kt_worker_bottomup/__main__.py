"""Orchestrator worker entry point.

Usage: python -m kt_worker_bottomup
"""

import logging

from kt_config.settings import get_settings
from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import worker_lifespan
from kt_worker_bottomup.bottom_up import (
    agent_select_wf,
    bottom_up_prepare_scope_wf,
    bottom_up_prepare_wf,
    bottom_up_scope_wf,
    bottom_up_wf,
)


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
    settings = get_settings()
    _core_workflows = [
        agent_select_wf,
        bottom_up_wf,
        bottom_up_scope_wf,
        bottom_up_prepare_scope_wf,
        bottom_up_prepare_wf,
    ]
    worker = hatchet.worker(
        "orchestrator",
        slots=settings.worker_bottomup_slots,
        durable_slots=settings.worker_bottomup_durable_slots,
        workflows=_core_workflows + plugin_manager.get_plugin_workflows(),
        lifespan=worker_lifespan,
    )
    worker.start()


if __name__ == "__main__":
    main()
