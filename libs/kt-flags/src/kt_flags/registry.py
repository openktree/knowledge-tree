"""Flag registry — the single source of truth for every Knowledge Tree flag.

Each ``FlagSpec`` declares a flag's public key (``feature.x``, ``plugin.y``,
``provider.z``, ``infra.w``, ``auth.v``), its type, default, description,
and — for Phase 0 — the ``kt_config.Settings`` field that backs it. A
future ``DbProvider`` will consult this registry to list every known flag
and to write an admin-UI-friendly schema.

The naming convention is enforced by ``test_registry.py``. New flags MUST
match one of the allowed section prefixes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class FlagType(str, Enum):
    BOOLEAN = "boolean"
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"


ALLOWED_SECTIONS: tuple[str, ...] = (
    "feature",
    "plugin",
    "provider",
    "infra",
    "auth",
)

_KEY_REGEX = re.compile(r"^(?P<section>" + "|".join(ALLOWED_SECTIONS) + r")\.[a-z0-9_]+(?:\.[a-z0-9_]+)*$")


@dataclass(frozen=True)
class FlagSpec:
    """Declarative record for a single flag.

    ``settings_field`` is the attribute on ``kt_config.Settings`` that backs
    the flag in Phase 0. ``dynamic=True`` means we expect a ``DbProvider``
    to shadow this flag at runtime (per-user / per-tenant rules, admin-UI
    overrides); the default ``False`` keeps it static.
    """

    key: str
    type: FlagType
    default: Any
    description: str
    settings_field: str | None = None
    dynamic: bool = False

    def __post_init__(self) -> None:
        if not _KEY_REGEX.match(self.key):
            raise ValueError(
                f"Invalid flag key {self.key!r} — must match <{'|'.join(ALLOWED_SECTIONS)}>.<snake_name>[.<subkey>]"
            )

    @property
    def section(self) -> str:
        return self.key.split(".", 1)[0]


# ── Flag registry ──────────────────────────────────────────────────────────
#
# Phase 0 ships three representative flags end-to-end — one feature toggle,
# one plugin gate, one provider gate. Remaining toggles stay on direct
# ``settings.*`` access until their call sites are migrated.

FLAG_REGISTRY: dict[str, FlagSpec] = {
    spec.key: spec
    for spec in (
        FlagSpec(
            key="feature.full_text_fetch",
            type=FlagType.BOOLEAN,
            default=True,
            description="Enable fetching full HTML/PDF for search hits before extraction.",
            settings_field="enable_full_text_fetch",
        ),
        FlagSpec(
            key="plugin.concept_extractor.enabled",
            type=FlagType.BOOLEAN,
            default=True,
            description="Register the backend-engine-concept-extractor plugin at worker boot.",
            settings_field=None,  # no Settings mirror yet — defaults to True unless overridden
        ),
        FlagSpec(
            key="provider.serper.enabled",
            type=FlagType.BOOLEAN,
            default=True,
            description=(
                "Register the Serper search provider. API-key presence check is separate — "
                "this gate is an operator kill-switch."
            ),
            settings_field=None,
        ),
    )
}


def get_spec(key: str) -> FlagSpec:
    """Return the ``FlagSpec`` for ``key`` or raise ``KeyError``."""
    return FLAG_REGISTRY[key]
