"""Conversations worker entry point.

Usage: python -m kt_worker_conv
"""

import logging

from kt_hatchet.client import get_hatchet
from kt_hatchet.lifespan import worker_lifespan
from kt_worker_conv.workflows.conversations import follow_up_wf, resynthesize_task


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    hatchet = get_hatchet()
    worker = hatchet.worker(
        "conversations",
        slots=10,
        durable_slots=10,
        workflows=[follow_up_wf, resynthesize_task],
        lifespan=worker_lifespan,
    )
    worker.start()


if __name__ == "__main__":
    main()
