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

    from kt_db.core_plugin import register_core_plugins
    from kt_plugins import load_default_plugins, plugin_manager
    from kt_providers.registry import bridge_plugin_search_providers

    register_core_plugins(plugin_manager)
    settings = get_settings()
    load_default_plugins(
        enabled_plugins=settings.enabled_plugins or None,
        license_keys=settings.plugin_license_keys or None,
    )
    bridge_plugin_search_providers()

    hatchet = get_hatchet()
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
