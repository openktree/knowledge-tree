"""Plugin-specific error classes."""


class PluginError(Exception):
    """Base exception for all plugin errors."""

    def __init__(self, plugin_id: str, message: str) -> None:
        self.plugin_id = plugin_id
        super().__init__(f"[plugin:{plugin_id}] {message}")


class PluginLoadError(PluginError):
    """Raised when a plugin fails to load or initialize."""


class PluginLicenseError(PluginError):
    """Raised when a commercial plugin has an invalid or missing license key."""
