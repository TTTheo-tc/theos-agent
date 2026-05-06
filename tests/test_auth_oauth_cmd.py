from src.auth.types import OAuthCredential
from src.cli import auth_oauth_cmd


def test_provider_login_accepts_hyphenated_oauth_name(monkeypatch) -> None:
    called = {"ok": False}

    def _handler() -> None:
        called["ok"] = True

    monkeypatch.setitem(auth_oauth_cmd._LOGIN_HANDLERS, "github_copilot", _handler)

    auth_oauth_cmd.provider_login("github-copilot")

    assert called["ok"] is True


def test_auth_login_normalizes_hyphenated_oauth_provider(monkeypatch) -> None:
    cred = OAuthCredential(
        provider="github_copilot",
        access="copilot-token",
        refresh="github-token",
        expires=9999999999999,
    )

    class _Plugin:
        def read_external_credentials(self) -> OAuthCredential:
            return cred

        def login(self, redirect_uri: str) -> None:
            del redirect_uri
            raise AssertionError("external credentials should be used first")

    saved = {}
    monkeypatch.setattr(
        "src.auth.plugins.register_builtin_plugins",
        lambda: {"github_copilot": _Plugin()},
    )
    monkeypatch.setattr(
        "src.auth.store.add_oauth_profile",
        lambda **kwargs: saved.update(kwargs) or "github_copilot:default",
    )

    auth_oauth_cmd.auth_login("github-copilot")

    assert saved["provider"] == "github_copilot"
    assert saved["access"] == "copilot-token"


def test_auth_refresh_uses_normalized_oauth_profile(monkeypatch) -> None:
    class _Manager:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def resolve(self, provider: str, profile_id: str) -> tuple[str, dict[str, str]]:
            assert provider == "github_copilot"
            assert profile_id == "github_copilot:default"
            return "token", {}

    cred = OAuthCredential(
        provider="github_copilot",
        access="copilot-token",
        refresh="github-token",
        expires=9999999999999,
    )
    monkeypatch.setattr(
        "src.auth.store.get_oauth_credential_for_provider",
        lambda _provider: (cred, "github_copilot:default"),
    )
    monkeypatch.setattr("src.auth.plugins.register_builtin_plugins", dict)
    monkeypatch.setattr("src.auth.oauth_manager.OAuthManager", _Manager)

    auth_oauth_cmd.auth_refresh("github-copilot")
