"""Flag client singleton.

Mirrors the ``get_settings()`` shape: a module-level accessor that returns
a cached client resolving against the currently-active OpenFeature provider.

The first call to :func:`get_flag_client` installs a cached
``SettingsProvider`` as the default provider. Subsequent calls to
:func:`set_provider` swap the active provider and track the current one so
:mod:`kt_flags.testing.override_flags` can snapshot-and-restore reliably
without clobbering a caller-set provider.
"""

from __future__ import annotations

import logging

from openfeature import api as _ofa
from openfeature.client import OpenFeatureClient
from openfeature.evaluation_context import EvaluationContext
from openfeature.provider import FeatureProvider

from kt_flags.providers.settings_provider import SettingsProvider

logger = logging.getLogger(__name__)

_FLAG_DOMAIN = "knowledge-tree"

# Module-level tracker for the currently-active default provider. Kept in
# parallel with OpenFeature's global registry so we don't have to reach
# into its private ``provider_registry`` to read the current value.
_current_provider: FeatureProvider | None = None
_default_settings_provider: SettingsProvider | None = None


def _get_default_settings_provider() -> SettingsProvider:
    """Cached ``SettingsProvider`` — reused instead of reconstructed on each swap."""
    global _default_settings_provider
    if _default_settings_provider is None:
        _default_settings_provider = SettingsProvider()
    return _default_settings_provider


def set_provider(provider: FeatureProvider) -> None:
    """Swap the active OpenFeature provider and update our tracker.

    All provider swaps should go through this helper so
    :func:`get_current_provider` stays correct.
    """
    global _current_provider
    _ofa.set_provider(provider)
    _current_provider = provider


def get_current_provider() -> FeatureProvider | None:
    """Return the provider most recently installed via :func:`set_provider`."""
    return _current_provider


def _ensure_default_provider() -> None:
    """Install ``SettingsProvider`` as the default on first access."""
    if _current_provider is None:
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
    """Drop the cached client and current-provider tracker. Tests only."""
    global _client, _current_provider, _default_settings_provider
    _client = None
    _current_provider = None
    _default_settings_provider = None
