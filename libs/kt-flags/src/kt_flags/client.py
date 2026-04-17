"""Flag client singleton.

Mirrors the ``get_settings()`` shape: a module-level accessor that returns
a cached client resolving against the currently-active OpenFeature provider.

The first call to :func:`get_flag_client` installs a cached
``SettingsProvider`` as the default provider. Subsequent swaps go through
:func:`set_provider` or ``openfeature.api.set_provider`` directly — either
way, :func:`get_current_provider` reads the active provider from the
OpenFeature registry, so :mod:`kt_flags.testing.override_flags` always
snapshots the *actual* current provider.
"""

from __future__ import annotations

import logging

from openfeature import api as _ofa
from openfeature.client import OpenFeatureClient
from openfeature.evaluation_context import EvaluationContext
from openfeature.provider import FeatureProvider
from openfeature.provider._registry import provider_registry as _registry

from kt_flags.providers.settings_provider import SettingsProvider

logger = logging.getLogger(__name__)

_FLAG_DOMAIN = "knowledge-tree"

_default_settings_provider: SettingsProvider | None = None


def _get_default_settings_provider() -> SettingsProvider:
    """Cached ``SettingsProvider`` — reused instead of reconstructed on each swap."""
    global _default_settings_provider
    if _default_settings_provider is None:
        _default_settings_provider = SettingsProvider()
    return _default_settings_provider


def set_provider(provider: FeatureProvider) -> None:
    """Swap the active OpenFeature provider.

    Thin alias for ``openfeature.api.set_provider`` re-exported here so
    callers can stay on the ``kt_flags`` surface. Reading the current
    provider still works if a caller bypasses this helper — see
    :func:`get_current_provider`.
    """
    _ofa.set_provider(provider)


def get_current_provider() -> FeatureProvider | None:
    """Return the provider currently installed as the OpenFeature default.

    Reads through the OpenFeature registry rather than a parallel tracker,
    so it stays correct even when callers reach for
    ``openfeature.api.set_provider`` directly (legal — it's the public API).
    """
    try:
        return _registry.get_default_provider()
    except Exception:  # noqa: BLE001 — never break callers on registry state
        return None


def _ensure_default_provider() -> None:
    """Install ``SettingsProvider`` as the default on first access.

    OpenFeature seeds a ``NoOpProvider`` by default; we replace it with
    our Settings-backed provider the first time anyone needs a flag.
    """
    current = get_current_provider()
    metadata = current.get_metadata() if current is not None else None
    if metadata is None or metadata.name == "No-op Provider":
        set_provider(_get_default_settings_provider())


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
    def raw(self) -> OpenFeatureClient:
        return self._inner


_client: FlagClient | None = None


def get_flag_client() -> FlagClient:
    """Return the process-wide ``FlagClient`` singleton.

    The underlying ``OpenFeatureClient`` resolves the active provider on
    every call, so the singleton survives provider swaps — no reset needed
    when :mod:`kt_flags.testing.override_flags` switches providers.
    """
    global _client
    if _client is None:
        _client = FlagClient()
    return _client


def reset_flag_client() -> None:
    """Drop the cached client + OpenFeature global state. Tests only.

    Calls ``openfeature.api.clear_providers()`` so the next
    :func:`get_flag_client` call starts against a fresh registry — no
    leftover provider instances or event-handler subscriptions from a
    prior test.
    """
    global _client, _default_settings_provider
    _client = None
    _default_settings_provider = None
    _ofa.clear_providers()
