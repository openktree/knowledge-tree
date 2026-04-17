"""Shared test fixtures for kt-plugins."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from kt_plugins import plugin_manager


@pytest.fixture(autouse=True)
def _reset_plugin_manager() -> Generator[None, None, None]:
    """Reset the module-level singleton between tests for isolation."""
    plugin_manager.clear()
    yield
    plugin_manager.clear()
