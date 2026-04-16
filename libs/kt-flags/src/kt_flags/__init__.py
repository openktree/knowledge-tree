"""Knowledge Tree feature-flag layer built on OpenFeature.

Provides a single, standard API for reading feature flags across every
backend package (``api``, every ``worker-*``, and plugins). Today the
backing store is ``kt_config.Settings`` (env + ``config.yaml``); the
``DbProvider`` seam is preserved so a runtime admin UI can layer on later
without touching call sites.

Typical usage::

    from kt_flags import get_flag_client

    flags = get_flag_client()
    if flags.get_boolean("feature.full_text_fetch", default=True):
        ...
"""

from __future__ import annotations

from kt_flags.client import FlagClient, get_flag_client
from kt_flags.context import EvalContext, build_eval_context
from kt_flags.registry import FLAG_REGISTRY, FlagSpec, FlagType

__all__ = [
    "FLAG_REGISTRY",
    "EvalContext",
    "FlagClient",
    "FlagSpec",
    "FlagType",
    "build_eval_context",
    "get_flag_client",
]
