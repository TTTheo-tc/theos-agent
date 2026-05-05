from __future__ import annotations

import json
import os
import time
from json import JSONDecodeError
from pathlib import Path
from unittest.mock import patch

import pytest

from src.feishu.token import get_access_token, save_access_token, save_refresh_token


def test_save_token_files_use_expected_schema_and_permissions(tmp_path: Path):
    expires_epoch = int(time.time()) + 3600

    save_access_token("access-123", expires_epoch, token_dir=str(tmp_path))
    save_refresh_token("refresh-123", expires_epoch, token_dir=str(tmp_path))

    access_path = tmp_path / "access_token.json"
    refresh_path = tmp_path / "refresh_token.json"
    access_data = json.loads(access_path.read_text(encoding="utf-8"))
    refresh_data = json.loads(refresh_path.read_text(encoding="utf-8"))

    assert access_data["access_token"] == "access-123"
    assert "timestamp" in access_data
    assert access_data["expires_epoch"] == expires_epoch
    assert "expires_datetime" in access_data
    assert refresh_data["refresh_token"] == "refresh-123"
    assert "timestamp" in refresh_data
    assert refresh_data["expires_epoch"] == expires_epoch
    assert "expires_datetime" in refresh_data
    assert oct(os.stat(access_path).st_mode & 0o777) == "0o600"
    assert oct(os.stat(refresh_path).st_mode & 0o777) == "0o600"


def test_get_access_token_uses_valid_cached_token(tmp_path: Path):
    save_access_token("cached-token", int(time.time()) + 3600, token_dir=str(tmp_path))

    with patch("src.feishu.token.refresh_token_from_api") as refresh:
        assert get_access_token("app", "secret", token_dir=str(tmp_path), min_ttl=30) == "cached-token"

    refresh.assert_not_called()


def test_token_paths_expand_user(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    save_access_token("home-token", int(time.time()) + 3600, token_dir="~/tokens")

    assert get_access_token("app", "secret", token_dir="~/tokens", min_ttl=30) == "home-token"
    assert (tmp_path / "tokens" / "access_token.json").exists()


def test_get_access_token_preserves_invalid_json_error(tmp_path: Path):
    token_path = tmp_path / "access_token.json"
    token_path.write_text("{bad json", encoding="utf-8")

    with pytest.raises(JSONDecodeError):
        get_access_token("app", "secret", token_dir=str(tmp_path), min_ttl=30)


def test_get_access_token_refreshes_expiring_token(tmp_path: Path):
    save_access_token("old-token", int(time.time()) + 5, token_dir=str(tmp_path))
    save_refresh_token("refresh-token", int(time.time()) + 3600, token_dir=str(tmp_path))

    with patch(
        "src.feishu.token.refresh_token_from_api",
        return_value={
            "access_token": "new-token",
            "refresh_token": "new-refresh",
            "expires_in": 7200,
            "refresh_token_expires_in": 2592000,
        },
    ) as refresh:
        assert get_access_token("app", "secret", token_dir=str(tmp_path), min_ttl=30) == "new-token"

    refresh.assert_called_once_with("refresh-token", app_id="app", app_secret="secret")
