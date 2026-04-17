"""Plugin framework errors."""

from __future__ import annotations


class PluginError(Exception):
    """Base class for all plugin framework errors."""

    def __init__(self, plugin_id: str, message: str) -> None:
        super().__init__(f"[{plugin_id}] {message}")
        self.plugin_id = plugin_id
        self.message = message


class PluginLoadError(PluginError):
    """Raised when a plugin fails to discover, register, or bootstrap."""


class PluginLicenseError(PluginError):
    """Raised when a commercial plugin's license key is missing or invalid."""
