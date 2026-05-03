from __future__ import annotations

from src.cli.init_channels import _configure_feishu_remote_auth, _suggest_feishu_redirect_uri
from src.config.schema import Config


def test_suggest_feishu_redirect_uri_prefers_existing_config():
    config = Config()
    config.channels.feishu.oauth_redirect_uri = "http://existing.example/feishu/oauth/callback"

    uri, source = _suggest_feishu_redirect_uri(config)

    assert uri == "http://existing.example/feishu/oauth/callback"
    assert source == "configured"


def test_suggest_feishu_redirect_uri_prefers_magicdns(monkeypatch):
    config = Config()
    monkeypatch.setattr("src.ui.tailscale.detect_magicdns_name", lambda: "aries.tail.ts.net")
    monkeypatch.setattr("src.ui.tailscale.detect_tailscale_ip", lambda: "100.68.1.2")

    uri, source = _suggest_feishu_redirect_uri(config)

    assert uri == "http://aries.tail.ts.net:18790/feishu/oauth/callback"
    assert source == "MagicDNS"


def test_configure_feishu_remote_auth_sets_redirect_and_gateway_host(monkeypatch):
    config = Config()
    config.gateway.host = "127.0.0.1"

    confirms = iter([True, True, True])
    monkeypatch.setattr(
        "src.cli.init_channels.typer.confirm", lambda *args, **kwargs: next(confirms)
    )
    monkeypatch.setattr(
        "src.cli.init_channels._suggest_feishu_redirect_uri",
        lambda cfg: ("http://aries.tail.ts.net:18790/feishu/oauth/callback", "MagicDNS"),
    )

    _configure_feishu_remote_auth(config)

    assert (
        config.channels.feishu.oauth_redirect_uri
        == "http://aries.tail.ts.net:18790/feishu/oauth/callback"
    )
    assert config.gateway.host == "0.0.0.0"


def test_configure_feishu_remote_auth_allows_manual_uri(monkeypatch):
    config = Config()

    confirms = iter([True])
    monkeypatch.setattr(
        "src.cli.init_channels.typer.confirm", lambda *args, **kwargs: next(confirms)
    )
    monkeypatch.setattr(
        "src.cli.init_channels._suggest_feishu_redirect_uri", lambda cfg: (None, None)
    )
    monkeypatch.setattr(
        "src.cli.init_channels.typer.prompt",
        lambda *args, **kwargs: "http://manual.example/feishu/oauth/callback",
    )

    _configure_feishu_remote_auth(config)

    assert (
        config.channels.feishu.oauth_redirect_uri == "http://manual.example/feishu/oauth/callback"
    )
