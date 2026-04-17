"""override_flags swaps the global provider for the scope of a with-block."""

from __future__ import annotations

from kt_flags.client import get_flag_client
from kt_flags.testing import override_flags


def test_override_flips_boolean() -> None:
    client = get_flag_client()
    # Baseline — flag default is True
    assert client.get_boolean("feature.full_text_fetch", default=True) is True

    with override_flags({"feature.full_text_fetch": False}):
        client_inside = get_flag_client()
        assert client_inside.get_boolean("feature.full_text_fetch", default=True) is False

    # Restored — SettingsProvider back in place
    restored = get_flag_client()
    assert restored.get_boolean("feature.full_text_fetch", default=True) is True


def test_override_applies_to_unknown_keys_too() -> None:
    with override_flags({"feature.experimental_x": True}):
        c = get_flag_client()
        # Unknown to SettingsProvider registry but present in in-memory store
        assert c.get_boolean("feature.experimental_x", default=False) is True
