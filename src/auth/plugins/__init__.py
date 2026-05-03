"""Built-in OAuth plugins."""

from __future__ import annotations

from src.auth.oauth_plugin import OAuthPlugin


def register_builtin_plugins() -> dict[str, OAuthPlugin]:
    """Return a dict of provider_id -> plugin for all built-in plugins."""
    plugins: dict[str, OAuthPlugin] = {}
    try:
        from src.auth.plugins.openai_codex import OpenAICodexPlugin

        p = OpenAICodexPlugin()
        plugins[p.provider_id] = p
    except Exception:
        pass
    try:
        from src.auth.plugins.github_copilot import GitHubCopilotPlugin

        p = GitHubCopilotPlugin()
        plugins[p.provider_id] = p
    except Exception:
        pass
    return plugins
