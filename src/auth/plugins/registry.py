"""Registry for built-in OAuth plugins."""

from __future__ import annotations

from contextlib import suppress

from src.auth.oauth_plugin import OAuthPlugin


def register_builtin_plugins() -> dict[str, OAuthPlugin]:
    """Return provider_id -> plugin for all available built-in plugins."""
    plugins: dict[str, OAuthPlugin] = {}
    with suppress(Exception):
        from src.auth.plugins.openai_codex import OpenAICodexPlugin

        plugin = OpenAICodexPlugin()
        plugins[plugin.provider_id] = plugin
    with suppress(Exception):
        from src.auth.plugins.github_copilot import GitHubCopilotPlugin

        plugin = GitHubCopilotPlugin()
        plugins[plugin.provider_id] = plugin
    return plugins
