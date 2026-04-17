"""Provider that resolves flags from ``kt_config.Settings``.

Single source of truth in Phase 0. Env overrides (``FEATURE_FULL_TEXT_FETCH=false``)
continue to work — they override the underlying Settings field, which this
provider then reads. Flags without a ``settings_field`` fall back to the
spec's ``default``; operators override those via the ``config.yaml``'s
``flags:`` section in a future phase.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagResolutionDetails, Reason
from openfeature.provider import AbstractProvider
from openfeature.provider.metadata import Metadata

from kt_config.settings import Settings, get_settings
from kt_flags.registry import FLAG_REGISTRY, FlagSpec, FlagType

_METADATA = Metadata(name="kt-settings-provider")


class SettingsProvider(AbstractProvider):
    """OpenFeature provider backed by ``kt_config.Settings``.

    ``Settings()`` re-parses ``.env`` + ``config.yaml`` on every construction.
    We cache the instance at provider init so flag resolution is a cheap
    attribute access. Tests that need a different Settings snapshot should
    construct a fresh provider (the ``override_flags`` fixture does exactly
    this when it swaps in an ``InMemoryProvider``).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self._settings = settings if settings is not None else get_settings()

    def get_metadata(self) -> Metadata:
        return _METADATA

    # ── internal --------------------------------------------------------

    def _spec_or_none(self, key: str) -> FlagSpec | None:
        return FLAG_REGISTRY.get(key)

    def _resolve(
        self,
        flag_key: str,
        default_value: Any,
        expected: FlagType,
    ) -> FlagResolutionDetails[Any]:
        spec = self._spec_or_none(flag_key)
        if spec is None:
            return FlagResolutionDetails(
                value=default_value,
                reason=Reason.DEFAULT,
                error_code=ErrorCode.FLAG_NOT_FOUND,
                error_message=f"flag {flag_key!r} not in FLAG_REGISTRY",
            )
        if spec.type != expected:
            return FlagResolutionDetails(
                value=default_value,
                reason=Reason.ERROR,
                error_code=ErrorCode.TYPE_MISMATCH,
                error_message=(f"flag {flag_key!r} is {spec.type.value}, caller asked for {expected.value}"),
            )
        if spec.settings_field is None:
            return FlagResolutionDetails(
                value=spec.default,
                reason=Reason.STATIC,
            )
        value = getattr(self._settings, spec.settings_field, spec.default)
        return FlagResolutionDetails(value=value, reason=Reason.STATIC)

    # ── OpenFeature API ------------------------------------------------

    def resolve_boolean_details(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[bool]:
        return self._resolve(flag_key, default_value, FlagType.BOOLEAN)

    def resolve_string_details(
        self,
        flag_key: str,
        default_value: str,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[str]:
        return self._resolve(flag_key, default_value, FlagType.STRING)

    def resolve_integer_details(
        self,
        flag_key: str,
        default_value: int,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[int]:
        return self._resolve(flag_key, default_value, FlagType.INTEGER)

    def resolve_float_details(
        self,
        flag_key: str,
        default_value: float,
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[float]:
        return self._resolve(flag_key, default_value, FlagType.FLOAT)

    def resolve_object_details(
        self,
        flag_key: str,
        default_value: Sequence[Any] | Mapping[str, Any],
        evaluation_context: EvaluationContext | None = None,
    ) -> FlagResolutionDetails[Sequence[Any] | Mapping[str, Any]]:
        return FlagResolutionDetails(
            value=default_value,
            reason=Reason.DEFAULT,
            error_code=ErrorCode.TYPE_MISMATCH,
            error_message="SettingsProvider does not support object flags",
        )
