"""Tests for Feishu permission granting after document creation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.feishu.api_write import add_permission_member, detect_member_type
from src.feishu.errors import FeishuAPIError

# ---------------------------------------------------------------------------
# detect_member_type
# ---------------------------------------------------------------------------


class TestDetectMemberType:
    def test_openid_prefix(self):
        assert detect_member_type("ou_abc123") == "openid"

    def test_chatid_prefix(self):
        assert detect_member_type("oc_abc123") == "chatid"

    def test_userid_fallback(self):
        assert detect_member_type("u_abc123") == "userid"

    def test_empty_string(self):
        assert detect_member_type("") == "userid"

    def test_plain_id(self):
        assert detect_member_type("some_user_id") == "userid"


# ---------------------------------------------------------------------------
# add_permission_member
# ---------------------------------------------------------------------------


class TestAddPermissionMember:
    def _mock_unmarshal(self, obj):
        """Return a plain dict instead of trying to JSON-serialize a MagicMock."""
        return {"member_type": "openid", "member_id": "ou_user1", "perm": "full_access"}

    def test_calls_api(self):
        """Verify add_permission_member calls drive.v1.permission_member.create."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data.member = MagicMock()

        mock_client.drive.v1.permission_member.create.return_value = mock_response

        with (
            patch("src.feishu.api_write._request_option", return_value=None),
            patch("src.feishu.api_write._unmarshal", side_effect=self._mock_unmarshal),
        ):
            result = add_permission_member(
                mock_client,
                file_token="doc_token_123",
                file_type="wiki",
                member_type="openid",
                member_id="ou_user1",
                perm="full_access",
            )

        mock_client.drive.v1.permission_member.create.assert_called_once()
        call_args = mock_client.drive.v1.permission_member.create.call_args
        request = call_args[0][0]
        # Verify the request was built with correct token and type
        assert request.token == "doc_token_123"
        assert request.type == "wiki"
        assert result == {"member_type": "openid", "member_id": "ou_user1", "perm": "full_access"}

    def test_calls_api_with_option(self):
        """Verify add_permission_member passes option when available."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data.member = MagicMock()

        mock_client.drive.v1.permission_member.create.return_value = mock_response
        mock_option = MagicMock()

        with (
            patch("src.feishu.api_write._request_option", return_value=mock_option),
            patch("src.feishu.api_write._unmarshal", side_effect=self._mock_unmarshal),
        ):
            add_permission_member(
                mock_client,
                file_token="doc_token_123",
                file_type="docx",
                member_type="chatid",
                member_id="oc_chat1",
                perm="edit",
            )

        # Called with (request, option)
        call_args = mock_client.drive.v1.permission_member.create.call_args
        assert len(call_args[0]) == 2
        assert call_args[0][1] is mock_option

    def test_raises_on_failure(self):
        """Verify _check raises FeishuAPIError on API failure."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 403
        mock_response.msg = "permission denied"

        mock_client.drive.v1.permission_member.create.return_value = mock_response

        with (
            patch("src.feishu.api_write._request_option", return_value=None),
            pytest.raises(FeishuAPIError, match="permission denied"),
        ):
            add_permission_member(
                mock_client,
                file_token="doc_token_123",
                file_type="wiki",
                member_type="openid",
                member_id="ou_user1",
            )


# ---------------------------------------------------------------------------
# add_permission_member_with_retry
# ---------------------------------------------------------------------------


class TestAddPermissionMemberWithRetry:
    @pytest.mark.asyncio
    async def test_retry_wrapper(self):
        """Verify retry-wrapped version calls through to add_permission_member."""
        from src.feishu.api_write import add_permission_member_with_retry

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.data.member = MagicMock()
        mock_client.drive.v1.permission_member.create.return_value = mock_response

        with (
            patch("src.feishu.api_write._request_option", return_value=None),
            patch(
                "src.feishu.api_write._unmarshal",
                return_value={"member_type": "openid", "member_id": "ou_x", "perm": "view"},
            ),
        ):
            await add_permission_member_with_retry(
                mock_client,
                file_token="tok",
                file_type="wiki",
                member_type="openid",
                member_id="ou_x",
                perm="view",
            )

        mock_client.drive.v1.permission_member.create.assert_called_once()


# ---------------------------------------------------------------------------
# create_page with grant_access_to
# ---------------------------------------------------------------------------


class TestCreatePageGrantsAccess:
    def _make_client(self):
        """Create a FeishuClient with mocked internals."""
        with patch("src.feishu.client.make_client"), patch("src.feishu.client.get_access_token"):
            from src.feishu.client import FeishuClient

            client = FeishuClient(app_id="id", app_secret="secret")
        return client

    def test_grants_access(self):
        """create_page grants access to specified members."""
        client = self._make_client()
        client.ensure_token = MagicMock()
        client.info_page = MagicMock(
            return_value=(
                "/tmp/cache.json",
                {"space_id": "sp1", "node_token": "nt1", "parent_node_token": "pnt1"},
            )
        )

        mock_node = {
            "obj_token": "doc123",
            "node_token": "node456",
        }

        with (
            patch("src.feishu.client.api_write.create_wiki_node", return_value=mock_node),
            patch("src.feishu.client.api_write.add_permission_member") as mock_perm,
            patch("src.feishu.client.api_write.detect_member_type", side_effect=detect_member_type),
        ):
            result = client.create_page(
                ref_url="https://feishu.cn/wiki/abc",
                title="Test",
                grant_access_to=["ou_user1", "oc_chat1"],
            )

        assert mock_perm.call_count == 2
        # First call: openid user
        first_call = mock_perm.call_args_list[0]
        assert first_call.kwargs["member_type"] == "openid"
        assert first_call.kwargs["member_id"] == "ou_user1"
        # Second call: chatid
        second_call = mock_perm.call_args_list[1]
        assert second_call.kwargs["member_type"] == "chatid"
        assert second_call.kwargs["member_id"] == "oc_chat1"

        assert result["permissions_granted"] == ["ou_user1", "oc_chat1"]
        assert "permissions_failed" not in result

    def test_grant_failure_doesnt_block(self):
        """Permission failure should not prevent create_page from returning."""
        client = self._make_client()
        client.ensure_token = MagicMock()
        client.info_page = MagicMock(
            return_value=(
                "/tmp/cache.json",
                {"space_id": "sp1", "node_token": "nt1", "parent_node_token": "pnt1"},
            )
        )

        mock_node = {
            "obj_token": "doc123",
            "node_token": "node456",
        }

        with (
            patch("src.feishu.client.api_write.create_wiki_node", return_value=mock_node),
            patch(
                "src.feishu.client.api_write.add_permission_member",
                side_effect=FeishuAPIError("forbidden", code=403),
            ),
            patch("src.feishu.client.api_write.detect_member_type", side_effect=detect_member_type),
        ):
            result = client.create_page(
                ref_url="https://feishu.cn/wiki/abc",
                title="Test",
                grant_access_to=["ou_user1"],
            )

        # Page was still created successfully
        assert result["node_token"] == "node456"
        assert result["url"].endswith("/wiki/node456")
        # Permission failure recorded but didn't raise
        assert result["permissions_failed"] == ["ou_user1"]
        assert result.get("permissions_granted") is None or result["permissions_granted"] == []

    def test_no_grant_when_not_specified(self):
        """create_page does not call permission API when grant_access_to is None."""
        client = self._make_client()
        client.ensure_token = MagicMock()
        client.info_page = MagicMock(
            return_value=(
                "/tmp/cache.json",
                {"space_id": "sp1", "node_token": "nt1", "parent_node_token": "pnt1"},
            )
        )

        mock_node = {
            "obj_token": "doc123",
            "node_token": "node456",
        }

        with (
            patch("src.feishu.client.api_write.create_wiki_node", return_value=mock_node),
            patch("src.feishu.client.api_write.add_permission_member") as mock_perm,
        ):
            result = client.create_page(
                ref_url="https://feishu.cn/wiki/abc",
                title="Test",
            )

        mock_perm.assert_not_called()
        assert "permissions_granted" not in result
        assert "permissions_failed" not in result


# ---------------------------------------------------------------------------
# FeishuCreateTool auto-grant from allowFrom
# ---------------------------------------------------------------------------


class TestCreateToolAutoGrants:
    @pytest.mark.asyncio
    async def test_auto_grants_to_allow_from(self):
        """FeishuCreateTool uses allow_from as default grant_access_to."""
        from src.agent.tools.feishu import FeishuCreateTool

        mock_client = MagicMock()
        mock_client.create_page.return_value = {
            "node_token": "nt1",
            "obj_token": "ot1",
            "url": "https://feishu.cn/wiki/nt1",
            "content_written": False,
            "permissions_granted": ["ou_owner"],
            "_verification_hint": "test",
        }

        tool = FeishuCreateTool(client=mock_client, allow_from=["ou_owner"])
        await tool.execute(ref_url="https://feishu.cn/wiki/abc", title="Test")

        # Verify create_page was called with grant_access_to derived from allow_from
        mock_client.create_page.assert_called_once()
        call_kwargs = mock_client.create_page.call_args
        # The call goes through _run which uses partial, so check positional + keyword args
        # _run calls: self._client.create_page(ref_url, title, markdown=..., position=..., grant_access_to=...)
        assert call_kwargs.kwargs.get("grant_access_to") == ["ou_owner"]

    @pytest.mark.asyncio
    async def test_explicit_grant_overrides_allow_from(self):
        """Explicit grant_access_to overrides the allow_from default."""
        from src.agent.tools.feishu import FeishuCreateTool

        mock_client = MagicMock()
        mock_client.create_page.return_value = {
            "node_token": "nt1",
            "obj_token": "ot1",
            "url": "https://feishu.cn/wiki/nt1",
            "content_written": False,
            "permissions_granted": ["ou_other"],
            "_verification_hint": "test",
        }

        tool = FeishuCreateTool(client=mock_client, allow_from=["ou_owner"])
        await tool.execute(
            ref_url="https://feishu.cn/wiki/abc",
            title="Test",
            grant_access_to=["ou_other"],
        )

        call_kwargs = mock_client.create_page.call_args
        assert call_kwargs.kwargs.get("grant_access_to") == ["ou_other"]

    @pytest.mark.asyncio
    async def test_no_auto_grant_when_allow_from_empty(self):
        """No auto-grant when allow_from is empty and grant_access_to not specified."""
        from src.agent.tools.feishu import FeishuCreateTool

        mock_client = MagicMock()
        mock_client.create_page.return_value = {
            "node_token": "nt1",
            "obj_token": "ot1",
            "url": "https://feishu.cn/wiki/nt1",
            "content_written": False,
            "_verification_hint": "test",
        }

        tool = FeishuCreateTool(client=mock_client, allow_from=[])
        await tool.execute(ref_url="https://feishu.cn/wiki/abc", title="Test")

        call_kwargs = mock_client.create_page.call_args
        assert call_kwargs.kwargs.get("grant_access_to") is None
