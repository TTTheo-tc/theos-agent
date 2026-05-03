from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.feishu.remote_auth import (
    callback_health_url,
    get_gateway_redirect_uri,
    is_callback_server_alive,
)


def _config(host: str = "0.0.0.0", port: int = 18790, oauth_redirect_uri: str = ""):
    return SimpleNamespace(
        channels=SimpleNamespace(feishu=SimpleNamespace(oauth_redirect_uri=oauth_redirect_uri)),
        gateway=SimpleNamespace(host=host, port=port),
    )


def test_get_gateway_redirect_uri_prefers_explicit_config():
    with patch(
        "src.config.loader.load_config",
        return_value=_config(oauth_redirect_uri="https://gw.example.com/feishu/oauth/callback"),
    ):
        assert get_gateway_redirect_uri() == "https://gw.example.com/feishu/oauth/callback"


def test_get_gateway_redirect_uri_uses_tailscale_for_bind_all():
    with (
        patch("src.config.loader.load_config", return_value=_config()),
        patch("src.ui.tailscale.detect_tailscale_ip", return_value="100.68.1.2"),
    ):
        assert get_gateway_redirect_uri() == "http://100.68.1.2:18790/feishu/oauth/callback"


def test_get_gateway_redirect_uri_returns_none_for_localhost_only():
    with (
        patch("src.config.loader.load_config", return_value=_config()),
        patch("src.ui.tailscale.detect_tailscale_ip", return_value=None),
    ):
        assert get_gateway_redirect_uri() is None


def test_callback_health_url_rewrites_path():
    assert (
        callback_health_url("http://100.68.1.2:18790/feishu/oauth/callback?x=1")
        == "http://100.68.1.2:18790/health"
    )


def test_is_callback_server_alive_success():
    response = SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})
    with patch("src.feishu.remote_auth.httpx.get", return_value=response):
        assert is_callback_server_alive("http://100.68.1.2:18790/feishu/oauth/callback") is True


def test_is_callback_server_alive_failure():
    with patch("src.feishu.remote_auth.httpx.get", side_effect=RuntimeError("boom")):
        assert is_callback_server_alive("http://100.68.1.2:18790/feishu/oauth/callback") is False
