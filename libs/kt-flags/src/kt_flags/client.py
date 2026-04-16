"""Flag client singleton.

Mirrors the ``get_settings()`` shape: a module-level accessor that returns
a cached client pointing at the default provider. At import time we seed
the default provider with a ``SettingsProvider`` so imports have a working
client without explicit init.

Tests and adapters can call ``set_provider(...)`` (re-exported from
``openfeature.api``) or use the ``override_flags`` context manager in
``kt_flags.testing`` to swap providers per scope.
"""

from __future__ import annotations

import logging
from typing import Any

from openfeature import api as _ofa
from openfeature.evaluation_context import EvaluationContext

from kt_flags.providers.settings_provider import SettingsProvider

logger = logging.getLogger(__name__)

_FLAG_DOMAIN = "knowledge-tree"
_DEFAULT_PROVIDER_SET = False


def _ensure_default_provider() -> None:
    """Register a ``SettingsProvider`` once, on first client access."""
    global _DEFAULT_PROVIDER_SET
    if _DEFAULT_PROVIDER_SET:
        return
    _ofa.set_provider(SettingsProvider())
    _DEFAULT_PROVIDER_SET = True


class FlagClient:
    """Thin wrapper over ``OpenFeatureClient`` with typed accessors.

    Methods are synchronous — the backing ``SettingsProvider`` resolves
    in-process. The wrapper keeps call sites short and hides the
    OpenFeature-specific types so a future provider swap (e.g. to an
    async DB-backed provider) can ship a matching async API without
    rippling through consumers.
    """

    def __init__(self, domain: str = _FLAG_DOMAIN) -> None:
        _ensure_default_provider()
        self._inner = _ofa.get_client(domain=domain)

    def get_boolean(
        self,
        key: str,
        default: bool,
        *,
        context: EvaluationContext | None = None,
    ) -> bool:
        return self._inner.get_boolean_value(key, default, context)

    def get_string(
        self,
        key: str,
        default: str,
        *,
        context: EvaluationContext | None = None,
    ) -> str:
        return self._inner.get_string_value(key, default, context)

    def get_integer(
        self,
        key: str,
        default: int,
        *,
        context: EvaluationContext | None = None,
    ) -> int:
        return self._inner.get_integer_value(key, default, context)

    def get_float(
        self,
        key: str,
        default: float,
        *,
        context: EvaluationContext | None = None,
    ) -> float:
        return self._inner.get_float_value(key, default, context)

    # Escape hatch when callers need the raw OpenFeature client for hooks,
    # events, or details-style evaluation. Keep ad-hoc access rare.
    @property
    def raw(self) -> Any:
        return self._inner


_client: FlagClient | None = None


def get_flag_client() -> FlagClient:
    """Return the process-wide ``FlagClient`` singleton."""
    global _client
    if _client is None:
        _client = FlagClient()
    return _client


def reset_flag_client() -> None:
    """Drop the cached client and default-provider latch. Tests only."""
    global _client, _DEFAULT_PROVIDER_SET
    _client = None
    _DEFAULT_PROVIDER_SET = False
